"""HTTP API (presentation boundary). Routes translate HTTP ↔ services and enforce permissions;
no business logic lives here. Every route except /health and /auth/login needs a signed-in user."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.permissions import GLOBAL_ROLES, PROJECT_ROLES, Perm
from ..auth.security import issue_token
from ..db import models as m
from ..engine.assembly import Assembly, AssemblyError, EquipmentComponent, LaborComponent, MaterialComponent
from ..engine.costing import CostingError
from ..engine.formula import FormulaDefinition, FormulaDefinitionError, FormulaEvaluationError, InputSpec, evaluate
from ..engine.rates import PRODUCTIVITY_CONDITIONS, RateNotFound
from ..engine.service import ValidationFailed
from ..engine.units import UnitError, all_units, convert
from ..modules.registry import MODULES
from ..services import estimates as est
from ..services import library as lib
from ..services import projects as wbs_svc
from ..services import reports as rpt
from ..services import users as user_svc
from ..services.ratebook import SqlAssemblyResolver, SqlFormulaResolver
from .deps import DB, CurrentUser, library_scope, project_access, require_global

router = APIRouter()

ENGINE_ERRORS = (UnitError, FormulaDefinitionError, FormulaEvaluationError, ValidationFailed, RateNotFound, CostingError,
                 AssemblyError, LookupError, est.EstimateLocked, ValueError)


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, est.EstimateLocked):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError) and not isinstance(exc, RateNotFound):
        return HTTPException(404, str(exc).strip("'\""))
    return HTTPException(422, str(exc))


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except IntegrityError as exc:
        raise HTTPException(422, f"rejected by database constraint: {exc.orig.__class__.__name__}") from None
    except ENGINE_ERRORS as exc:
        raise _fail(exc) from None


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (uuid.UUID, date)):
        return str(v)
    return v


def _version(s: Session, project_id: uuid.UUID, version_id: uuid.UUID) -> m.EstimateVersion:
    v = s.get(m.EstimateVersion, version_id)
    if v is None or v.project_id != project_id:  # never reveal another project's data
        raise HTTPException(404, "estimate version not found")
    return v


# ---------------------------------------------------------------- auth and users


@router.get("/health")
def health():
    return {"status": "ok"}


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, s: DB):
    user = user_svc.authenticate(s, body.email, body.password)
    if user is None:
        raise HTTPException(401, "email or password is incorrect")
    return {"access_token": issue_token(str(user.id), user.token_version), "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "name": user.name, "global_role": user.global_role}}


@router.get("/auth/me")
def me(user: CurrentUser, s: DB):
    memberships = s.execute(select(m.ProjectMember.project_id, m.ProjectMember.role).where(m.ProjectMember.user_id == user.id)).all()
    return {"id": str(user.id), "email": user.email, "name": user.name, "global_role": user.global_role,
            "projects": {str(p): r for p, r in memberships}}


@router.post("/auth/logout-everywhere")
def logout_everywhere(user: CurrentUser):
    user.token_version += 1
    return {"status": "all sessions signed out"}


class UserIn(BaseModel):
    email: str
    name: str
    password: str
    global_role: str = Field("viewer", pattern="|".join(GLOBAL_ROLES))


@router.post("/users", status_code=201)
def create_user(body: UserIn, user: CurrentUser, s: DB):
    require_global(user, Perm.MANAGE_USERS)
    u = _guard(user_svc.create_user, s, body.email, body.name, body.password, body.global_role, user.email)
    return {"id": str(u.id), "email": u.email, "global_role": u.global_role}


@router.get("/users")
def list_users(user: CurrentUser, s: DB):
    require_global(user, Perm.MANAGE_USERS)
    return [{"id": str(u.id), "email": u.email, "name": u.name, "global_role": u.global_role, "is_active": u.is_active}
            for u in s.scalars(select(m.User).order_by(m.User.email))]


# ---------------------------------------------------------------- reference


@router.get("/units")
def units(user: CurrentUser):
    return [{"symbol": u.symbol, "name": u.name, "kind": u.dimension.describe(), "system": u.system} for u in all_units()]


class ConvertIn(BaseModel):
    value: Decimal
    from_unit: str
    to_unit: str


@router.post("/units/convert")
def convert_units(body: ConvertIn, user: CurrentUser):
    try:
        return {"value": format(convert(body.value, body.from_unit, body.to_unit).normalize(), "f"), "unit": body.to_unit}
    except UnitError as exc:
        raise HTTPException(422, str(exc))


@router.get("/modules")
def modules(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    """Module catalogue. With project_id, each item type also says whether its assembly exists for that project."""
    resolver = None
    if project_id:
        project_access(s, user, project_id)
        resolver = SqlAssemblyResolver(s, project_id)

    def available(code: str | None) -> bool | None:
        if resolver is None or code is None:
            return None
        try:
            resolver.revision_row(code)
            return True
        except KeyError:
            return False

    return [
        {
            "number": mod.number, "code": mod.code, "name": mod.name, "trade": mod.trade, "kind": mod.kind,
            "status": mod.status, "services": list(mod.services),
            "item_types": [
                {"code": it.code, "label": it.label, "formula": it.formula_code, "default_assembly": it.default_assembly,
                 "priced_directly": it.priced_directly, "classification": it.classification,
                 "assembly_available": available(it.default_assembly)}
                for it in mod.item_types
            ],
        }
        for mod in sorted(MODULES.values(), key=lambda x: x.number)
    ]


@router.get("/conditions")
def conditions(user: CurrentUser):
    return list(PRODUCTIVITY_CONDITIONS)


# ---------------------------------------------------------------- formulas


class InputSpecIn(BaseModel):
    name: str
    kind: str
    unit: str
    label: str = ""
    required: bool = True
    default: Decimal | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None


class FormulaIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,59}$")
    name: str
    expression: str
    output_kind: str
    output_unit: str
    inputs: list[InputSpecIn]
    description: str = ""
    project_id: uuid.UUID | None = None


@router.get("/formulas")
def list_formulas(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.Formula).where(m.Formula.project_id.is_(None) | (m.Formula.project_id == project_id))
    out = []
    for f in s.scalars(q.order_by(m.Formula.code)):
        latest = f.versions[-1] if f.versions else None
        out.append({"code": f.code, "name": f.name, "scope": "project" if f.project_id else "master",
                    "version": latest.version if latest else None, "expression": latest.expression if latest else None,
                    "output_unit": latest.output_unit if latest else None, "inputs": latest.inputs if latest else []})
    return out


@router.post("/formulas", status_code=201)
def publish_formula(body: FormulaIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    definition = FormulaDefinition(body.code, body.name, body.expression, body.output_kind, body.output_unit,
                                   tuple(InputSpec(**i.model_dump()) for i in body.inputs), description=body.description)
    v = _guard(lib.publish_formula, s, definition, body.project_id, user.email)
    return {"code": body.code, "version": v.version, "content_hash": v.content_hash}


@router.get("/formulas/{code}/versions")
def formula_versions(code: str, user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    try:
        f = SqlFormulaResolver(s, project_id).formula_row(code)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    return [{"version": v.version, "expression": v.expression, "content_hash": v.content_hash,
             "created_by": v.created_by, "created_at": v.created_at.isoformat()} for v in f.versions]


class EvaluateIn(BaseModel):
    inputs: dict[str, dict | Decimal]
    version: int | None = None
    project_id: uuid.UUID | None = None


@router.post("/formulas/{code}/evaluate")
def evaluate_formula(code: str, body: EvaluateIn, user: CurrentUser, s: DB):
    if body.project_id:
        project_access(s, user, body.project_id)
    try:
        f = SqlFormulaResolver(s, body.project_id)(code, body.version)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    raw = {k: (str(v["value"]), v["unit"]) if isinstance(v, dict) else v for k, v in body.inputs.items()}
    return _guard(lambda: evaluate(f, raw).to_dict())


# ---------------------------------------------------------------- library (master when project_id is null)


class PriceIn(BaseModel):
    material_code: str
    unit: str
    price: Decimal
    currency: str
    valid_from: date
    valid_to: date | None = None
    brand: str | None = None
    project_id: uuid.UUID | None = None


@router.post("/library/prices", status_code=201)
def add_price(body: PriceIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_material_price, s, body.material_code, body.unit, body.price, body.currency, body.valid_from,
                 body.project_id, brand=body.brand, valid_to=body.valid_to, actor=user.email)
    return {"id": str(row.id), "revision": row.revision}


@router.get("/library/prices")
def list_prices(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.MaterialPrice).where(m.MaterialPrice.project_id.is_(None) | (m.MaterialPrice.project_id == project_id))
    return jsonable([{"id": r.id, "material_code": r.material_code, "unit": r.unit, "price": r.price, "currency": r.currency,
                      "valid_from": r.valid_from, "valid_to": r.valid_to, "revision": r.revision, "brand": r.brand,
                      "scope": "project" if r.project_id else "master"}
                     for r in s.scalars(q.order_by(m.MaterialPrice.material_code, m.MaterialPrice.valid_from))])


class MaterialIn(BaseModel):
    code: str
    description: str
    unit: str
    category: str = "material"
    project_id: uuid.UUID | None = None


@router.post("/library/materials", status_code=201)
def add_material(body: MaterialIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_material, s, body.code, body.description, body.unit, body.category, body.project_id)
    return {"id": str(row.id)}


class LaborRateIn(BaseModel):
    labor_class: str
    hourly_rate: Decimal
    currency: str
    valid_from: date
    project_id: uuid.UUID | None = None


@router.post("/library/labor-rates", status_code=201)
def add_labor_rate(body: LaborRateIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_labor_rate, s, body.labor_class, body.hourly_rate, body.currency, body.valid_from, body.project_id)
    return {"id": str(row.id)}


class CrewIn(BaseModel):
    code: str
    name: str
    members: list[tuple[str, Decimal]]
    project_id: uuid.UUID | None = None


@router.post("/library/crews", status_code=201)
def add_crew(body: CrewIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_crew, s, body.code, body.name, body.members, body.project_id)
    return {"id": str(row.id), "revision": row.revision}


class ProductivityIn(BaseModel):
    code: str
    output_per_crew_day: Decimal
    unit: str
    condition: str = "normal"
    project_id: uuid.UUID | None = None


@router.post("/library/productivity", status_code=201)
def add_productivity(body: ProductivityIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_productivity, s, body.code, body.output_per_crew_day, body.unit, body.condition, body.project_id)
    return {"id": str(row.id), "revision": row.revision}


class EquipmentRateIn(BaseModel):
    equipment_code: str
    rental_per_hour: Decimal
    currency: str
    valid_from: date
    fuel_litres_per_hour: Decimal = Decimal(0)
    fuel_item_code: str | None = None
    operating_per_hour: Decimal = Decimal(0)
    project_id: uuid.UUID | None = None


@router.post("/library/equipment-rates", status_code=201)
def add_equipment_rate(body: EquipmentRateIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.add_equipment_rate, s, body.equipment_code, body.rental_per_hour, body.currency, body.valid_from,
                 body.fuel_litres_per_hour, body.fuel_item_code, body.operating_per_hour, body.project_id)
    return {"id": str(row.id)}


class MarkupIn(BaseModel):
    code: str
    label: str
    pct: Decimal
    basis: str = "direct"


class CostParamsIn(BaseModel):
    currency: str
    hours_per_day: Decimal
    markups: list[MarkupIn]
    productivity_factors: dict[str, Decimal] = {}
    money_places: int = 2
    project_id: uuid.UUID | None = None


@router.post("/library/cost-parameters", status_code=201)
def set_cost_parameters(body: CostParamsIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    row = _guard(lib.set_cost_parameters, s, body.currency, body.hours_per_day, [mk.model_dump() for mk in body.markups],
                 body.productivity_factors, body.project_id, body.money_places)
    return {"id": str(row.id), "revision": row.revision}


class AssemblyIn(BaseModel):
    code: str
    description: str
    trade: str
    unit: str
    materials: list[dict] = []
    labor: list[dict] = []
    equipment: list[dict] = []
    notes: str = ""
    project_id: uuid.UUID | None = None


def _dec(x: dict, *keys: str) -> dict:
    return {**x, **{k: Decimal(str(x[k])) for k in keys if x.get(k) is not None}}


@router.post("/library/assemblies", status_code=201)
def save_assembly(body: AssemblyIn, user: CurrentUser, s: DB):
    library_scope(s, user, body.project_id)
    try:
        a = Assembly(
            code=body.code, description=body.description, trade=body.trade, unit=body.unit,
            materials=tuple(MaterialComponent(**_dec(x, "consumption", "wastage_pct")) for x in body.materials),
            labor=tuple(LaborComponent(**x) for x in body.labor),
            equipment=tuple(EquipmentComponent(**_dec(x, "hours_per_unit", "count")) for x in body.equipment),
        )
    except TypeError as exc:
        raise HTTPException(422, f"invalid component: {exc}")
    rev = _guard(lib.save_assembly, s, a, body.project_id, body.notes, user.email)
    return {"id": str(rev.id), "revision": rev.revision}


# ---------------------------------------------------------------- projects, members and WBS


class ProjectIn(BaseModel):
    code: str
    name: str
    currency: str = Field(min_length=3, max_length=3)
    unit_system: str = "metric"


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: CurrentUser, s: DB):
    require_global(user, Perm.CREATE_PROJECT)
    p = _guard(est.create_project, s, body.code, body.name, body.currency, body.unit_system)
    user_svc.set_member(s, p.id, user.id, "manager", user.email)
    return {"id": str(p.id), "code": p.code}


@router.get("/projects")
def list_projects(user: CurrentUser, s: DB):
    q = select(m.Project).order_by(m.Project.code)
    if user.global_role != "admin":
        q = q.join(m.ProjectMember, m.ProjectMember.project_id == m.Project.id).where(m.ProjectMember.user_id == user.id)
    return [{"id": str(p.id), "code": p.code, "name": p.name, "currency": p.currency} for p in s.scalars(q)]


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: str = Field(pattern="|".join(PROJECT_ROLES))


@router.put("/projects/{project_id}/members", status_code=200)
def set_member(project_id: uuid.UUID, body: MemberIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.MANAGE_MEMBERS)
    if s.get(m.User, body.user_id) is None:
        raise HTTPException(404, "user not found")
    mem = _guard(user_svc.set_member, s, project_id, body.user_id, body.role, user.email)
    return {"user_id": str(mem.user_id), "role": mem.role}


@router.get("/projects/{project_id}/wbs")
def get_wbs(project_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    return wbs_svc.wbs(s, project_id)


class BuildingIn(BaseModel):
    code: str
    name: str


@router.post("/projects/{project_id}/buildings", status_code=201)
def add_building(project_id: uuid.UUID, body: BuildingIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    return {"id": str(_guard(wbs_svc.add_building, s, project_id, body.code, body.name).id)}


class FloorIn(BaseModel):
    building_id: uuid.UUID
    code: str
    name: str
    level: int
    is_basement: bool = False
    gross_floor_area_m2: Decimal | None = None


@router.post("/projects/{project_id}/floors", status_code=201)
def add_floor(project_id: uuid.UUID, body: FloorIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    f = _guard(wbs_svc.add_floor, s, project_id, body.building_id, body.code, body.name, body.level, body.is_basement,
               body.gross_floor_area_m2)
    return {"id": str(f.id)}


class ZoneIn(BaseModel):
    floor_id: uuid.UUID
    code: str
    name: str


@router.post("/projects/{project_id}/zones", status_code=201)
def add_zone(project_id: uuid.UUID, body: ZoneIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    return {"id": str(_guard(wbs_svc.add_zone, s, project_id, body.floor_id, body.code, body.name).id)}


class RoomIn(BaseModel):
    floor_id: uuid.UUID
    code: str
    name: str
    zone_id: uuid.UUID | None = None


@router.post("/projects/{project_id}/rooms", status_code=201)
def add_room(project_id: uuid.UUID, body: RoomIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    return {"id": str(_guard(wbs_svc.add_room, s, project_id, body.floor_id, body.code, body.name, body.zone_id).id)}


# ---------------------------------------------------------------- estimates


class EstimateIn(BaseModel):
    code: str
    name: str
    pricing_date: date


@router.post("/projects/{project_id}/estimates", status_code=201)
def create_estimate(project_id: uuid.UUID, body: EstimateIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    v = _guard(est.create_estimate, s, project_id, body.code, body.name, body.pricing_date)
    return {"estimate_id": str(v.estimate_id), "version_id": str(v.id), "version_no": v.version_no}


@router.get("/projects/{project_id}/estimates")
def list_estimates(project_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    rows = s.execute(
        select(m.EstimateRecord, m.EstimateVersion)
        .join(m.EstimateVersion, m.EstimateVersion.estimate_id == m.EstimateRecord.id)
        .where(m.EstimateRecord.project_id == project_id)
        .order_by(m.EstimateRecord.code, m.EstimateVersion.version_no)
    ).all()
    return jsonable([{"estimate_id": e.id, "code": e.code, "name": e.name, "version_id": v.id, "version_no": v.version_no,
                      "status": v.status, "pricing_date": v.pricing_date, "total_amount": v.total_amount,
                      "approved_by": v.approved_by} for e, v in rows])


class ItemIn(BaseModel):
    item_key: str
    module: str
    item_type: str
    inputs: dict[str, dict]  # {"length": {"value": "6", "unit": "m"}}
    description: str | None = None
    condition: str = "normal"
    assembly_code: str | None = None
    priced_item_code: str | None = None
    building_id: uuid.UUID | None = None
    floor_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None


def _item_out(it: m.EstimateItemRecord) -> dict:
    return {"item_key": it.item_key, "module": it.module, "item_type": it.item_type, "trade": it.trade,
            "description": it.description, "quantity": it.breakdown["quantity"], "unit": it.unit,
            "unit_rate": it.breakdown["totals"]["unit_rate"], "amount": it.breakdown["totals"]["total"],
            "inputs": it.inputs, "condition": it.condition, "priced_item_code": it.priced_item_code,
            "building_id": str(it.building_id) if it.building_id else None,
            "floor_id": str(it.floor_id) if it.floor_id else None,
            "zone_id": str(it.zone_id) if it.zone_id else None,
            "room_id": str(it.room_id) if it.room_id else None, "breakdown": it.breakdown}


@router.post("/projects/{project_id}/versions/{version_id}/items")
def calculate_item(project_id: uuid.UUID, version_id: uuid.UUID, body: ItemIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _version(s, project_id, version_id)
    with s.begin_nested():  # a constraint failure must not poison the request transaction
        it = _guard(est.calculate_item, s, version_id, project_id=project_id, actor=user.email, **body.model_dump())
    return _item_out(it)


@router.delete("/projects/{project_id}/versions/{version_id}/items/{item_key}", status_code=204)
def delete_item(project_id: uuid.UUID, version_id: uuid.UUID, item_key: str, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _version(s, project_id, version_id)
    _guard(est.remove_item, s, version_id, item_key, user.email)


@router.get("/projects/{project_id}/versions/{version_id}/items")
def list_items(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    _version(s, project_id, version_id)
    return [_item_out(i) for i in est.items(s, version_id)]


@router.get("/projects/{project_id}/versions/{version_id}/boq")
def boq(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    _version(s, project_id, version_id)
    return jsonable(est.boq(s, version_id))


@router.get("/projects/{project_id}/versions/{version_id}/summary")
def summary(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    v = _version(s, project_id, version_id)
    return jsonable({
        "status": v.status,
        "totals": est.cost_summary(s, version_id),
        "materials": est.resource_summary(s, version_id, "material"),
        "labor": est.resource_summary(s, version_id, "labor"),
        "equipment": est.resource_summary(s, version_id, "equipment"),
        "cost_per_area": est.cost_per_area(s, version_id),
    })


@router.get("/projects/{project_id}/versions/{version_id}/consolidate")
def consolidate(project_id: uuid.UUID, version_id: uuid.UUID, by: str, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    _version(s, project_id, version_id)
    dims = tuple(d.strip() for d in by.split(",") if d.strip())
    return jsonable(_guard(est.consolidate, s, version_id, dims))


@router.post("/projects/{project_id}/versions/{version_id}/approve")
def approve(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.APPROVE_ESTIMATE)
    _version(s, project_id, version_id)
    v = _guard(est.approve, s, version_id, user.email)
    return {"version_id": str(v.id), "status": v.status, "total_amount": str(v.total_amount), "approved_by": v.approved_by}


class RevisionIn(BaseModel):
    pricing_date: date | None = None


@router.post("/projects/{project_id}/versions/{version_id}/revisions", status_code=201)
def new_revision(project_id: uuid.UUID, version_id: uuid.UUID, body: RevisionIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _version(s, project_id, version_id)
    v = _guard(est.new_revision, s, version_id, body.pricing_date, user.email)
    return {"version_id": str(v.id), "version_no": v.version_no}


@router.get("/projects/{project_id}/compare")
def compare(project_id: uuid.UUID, old: uuid.UUID, new: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    _version(s, project_id, old)
    _version(s, project_id, new)
    return jsonable(est.compare_versions(s, old, new))


# ---------------------------------------------------------------- exports


def _export(project_id: uuid.UUID, version_id: uuid.UUID, user, s: Session, kind: str) -> Response:
    project_access(s, user, project_id)
    v = _version(s, project_id, version_id)
    data = rpt.estimate_report_data(s, version_id)
    if kind == "xlsx":
        body, media = rpt.to_excel(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        body, media = rpt.to_pdf(data), "application/pdf"
    rpt.record_report(s, version_id, f"estimate.{kind}", data, user.email)
    p = s.get(m.Project, project_id)
    name = f"{p.code}_estimate_v{v.version_no}_{v.status}.{kind}".replace(" ", "_")
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/projects/{project_id}/versions/{version_id}/export.xlsx")
def export_xlsx(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    return _export(project_id, version_id, user, s, "xlsx")


@router.get("/projects/{project_id}/versions/{version_id}/export.pdf")
def export_pdf(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB):
    return _export(project_id, version_id, user, s, "pdf")


# ---------------------------------------------------------------- library listings (for the library screens)


def _scope_filter(col, project_id):
    return col.is_(None) | (col == project_id) if project_id else col.is_(None)


def _scope(row) -> str:
    return "project" if row.project_id else "master"


@router.get("/library/materials")
def list_materials(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.Material).where(_scope_filter(m.Material.project_id, project_id)).order_by(m.Material.code)
    return [{"id": str(r.id), "code": r.code, "description": r.description, "unit": r.unit, "category": r.category,
             "scope": _scope(r)} for r in s.scalars(q)]


@router.get("/library/labor-rates")
def list_labor_rates(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.LaborRate).where(_scope_filter(m.LaborRate.project_id, project_id))
    return jsonable([{"id": r.id, "labor_class": r.labor_class_code, "hourly_rate": r.hourly_rate, "currency": r.currency,
                      "valid_from": r.valid_from, "valid_to": r.valid_to, "scope": _scope(r)}
                     for r in s.scalars(q.order_by(m.LaborRate.labor_class_code, m.LaborRate.valid_from))])


@router.get("/library/crews")
def list_crews(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.CrewComposition).where(_scope_filter(m.CrewComposition.project_id, project_id))
    return jsonable([{"id": c.id, "code": c.code, "name": c.name, "revision": c.revision, "scope": _scope(c),
                      "members": [{"labor_class": x.labor_class_code, "count": x.count} for x in c.members]}
                     for c in s.scalars(q.order_by(m.CrewComposition.code, m.CrewComposition.revision))])


@router.get("/library/productivity")
def list_productivity(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.ProductivityRate).where(_scope_filter(m.ProductivityRate.project_id, project_id))
    return jsonable([{"id": r.id, "code": r.code, "condition": r.condition, "output_per_crew_day": r.output_per_crew_day,
                      "unit": r.unit, "revision": r.revision, "scope": _scope(r)}
                     for r in s.scalars(q.order_by(m.ProductivityRate.code, m.ProductivityRate.condition))])


@router.get("/library/equipment-rates")
def list_equipment_rates(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.EquipmentRate).where(_scope_filter(m.EquipmentRate.project_id, project_id))
    return jsonable([{"id": r.id, "equipment_code": r.equipment_code, "rental_per_hour": r.rental_per_hour,
                      "fuel_litres_per_hour": r.fuel_litres_per_hour, "fuel_item_code": r.fuel_item_code,
                      "operating_per_hour": r.operating_per_hour, "currency": r.currency, "valid_from": r.valid_from,
                      "scope": _scope(r)} for r in s.scalars(q.order_by(m.EquipmentRate.equipment_code))])


@router.get("/library/assemblies")
def list_assemblies(user: CurrentUser, s: DB, project_id: uuid.UUID | None = None):
    if project_id:
        project_access(s, user, project_id)
    q = select(m.AssemblyRecord).where(_scope_filter(m.AssemblyRecord.project_id, project_id)).order_by(m.AssemblyRecord.code)
    out = []
    for a in s.scalars(q):
        rev = a.revisions[-1] if a.revisions else None
        out.append({
            "id": str(a.id), "code": a.code, "description": a.description, "trade": a.trade, "unit": a.unit,
            "scope": _scope(a), "revision": rev.revision if rev else None,
            "components": jsonable([
                {k: getattr(c, k) for k in ("kind", "description", "item_code", "consumption", "unit", "wastage_pct",
                                            "category", "task", "crew_code", "productivity_code", "equipment_code",
                                            "basis", "hours_per_unit", "labor_task", "count")}
                for c in (rev.components if rev else [])
            ]),
        })
    return out
