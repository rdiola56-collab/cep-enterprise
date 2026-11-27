"""HTTP API (presentation boundary). Routes only translate HTTP ↔ services; no business logic lives here.

Authentication and role-based permissions are not implemented yet (next phase).
The acting user is taken from the ``X-CEP-User`` header for audit records only.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..db.session import get_session
from ..engine.assembly import Assembly, AssemblyError, EquipmentComponent, LaborComponent, MaterialComponent
from ..engine.costing import CostingError
from ..engine.formula import FormulaDefinition, FormulaDefinitionError, FormulaEvaluationError, InputSpec, evaluate
from ..engine.rates import RateNotFound
from ..engine.service import ValidationFailed
from ..engine.units import UnitError, all_units, convert
from ..modules.registry import MODULES
from ..services import estimates as est
from ..services import library as lib
from ..services.ratebook import SqlFormulaResolver

router = APIRouter()
DB = Annotated[Session, Depends(get_session)]
Actor = Annotated[str, Header(alias="X-CEP-User")]


def _actor(x_cep_user: str | None) -> str:
    return x_cep_user or "anonymous"


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, (LookupError,)) and not isinstance(exc, RateNotFound):
        return HTTPException(404, str(exc).strip("'\""))
    if isinstance(exc, est.EstimateLocked):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


ENGINE_ERRORS = (UnitError, FormulaDefinitionError, FormulaEvaluationError, ValidationFailed, RateNotFound, CostingError,
                 AssemblyError, LookupError, est.EstimateLocked, ValueError)


def _project(s: Session, project_id: uuid.UUID) -> m.Project:
    p = s.get(m.Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _version(s: Session, project_id: uuid.UUID, version_id: uuid.UUID) -> m.EstimateVersion:
    v = s.get(m.EstimateVersion, version_id)
    if v is None or v.project_id != project_id:  # never reveal another project's data
        raise HTTPException(404, "estimate version not found")
    return v


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (uuid.UUID, date)):
        return str(v)
    return v


# ---------------------------------------------------------------- reference


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/units")
def units():
    return [{"symbol": u.symbol, "name": u.name, "kind": u.dimension.describe(), "system": u.system} for u in all_units()]


class ConvertIn(BaseModel):
    value: Decimal
    from_unit: str
    to_unit: str


@router.post("/units/convert")
def convert_units(body: ConvertIn):
    try:
        return {"value": format(convert(body.value, body.from_unit, body.to_unit).normalize(), "f"), "unit": body.to_unit}
    except UnitError as exc:
        raise HTTPException(422, str(exc))


@router.get("/modules")
def modules():
    return [
        {
            "number": mod.number,
            "code": mod.code,
            "name": mod.name,
            "trade": mod.trade,
            "status": mod.status,
            "item_types": [
                {"code": it.code, "label": it.label, "formula": it.formula_code, "default_assembly": it.default_assembly,
                 "classification": it.classification}
                for it in mod.item_types
            ],
        }
        for mod in sorted(MODULES.values(), key=lambda x: x.number)
    ]


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
def list_formulas(s: DB, project_id: uuid.UUID | None = None):
    q = select(m.Formula).where(m.Formula.project_id.is_(None) | (m.Formula.project_id == project_id))
    out = []
    for f in s.scalars(q.order_by(m.Formula.code)):
        latest = f.versions[-1] if f.versions else None
        out.append({"code": f.code, "name": f.name, "scope": "project" if f.project_id else "master",
                    "version": latest.version if latest else None, "expression": latest.expression if latest else None,
                    "output_unit": latest.output_unit if latest else None, "inputs": latest.inputs if latest else []})
    return out


@router.post("/formulas", status_code=201)
def publish_formula(body: FormulaIn, s: DB, x_cep_user: Actor = None):
    definition = FormulaDefinition(body.code, body.name, body.expression, body.output_kind, body.output_unit,
                                   tuple(InputSpec(**i.model_dump()) for i in body.inputs), description=body.description)
    if body.project_id:
        _project(s, body.project_id)
    try:
        v = lib.publish_formula(s, definition, body.project_id, _actor(x_cep_user))
    except ENGINE_ERRORS as exc:
        raise _fail(exc)
    return {"code": body.code, "version": v.version, "content_hash": v.content_hash}


@router.get("/formulas/{code}/versions")
def formula_versions(code: str, s: DB, project_id: uuid.UUID | None = None):
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
def evaluate_formula(code: str, body: EvaluateIn, s: DB):
    try:
        f = SqlFormulaResolver(s, body.project_id)(code, body.version)
        raw = {k: (str(v["value"]), v["unit"]) if isinstance(v, dict) else v for k, v in body.inputs.items()}
        return evaluate(f, raw).to_dict()
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    except ENGINE_ERRORS as exc:
        raise _fail(exc)


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
def add_price(body: PriceIn, s: DB, x_cep_user: Actor = None):
    try:
        row = lib.add_material_price(s, body.material_code, body.unit, body.price, body.currency, body.valid_from,
                                     body.project_id, brand=body.brand, valid_to=body.valid_to, actor=_actor(x_cep_user))
    except ENGINE_ERRORS as exc:
        raise _fail(exc)
    return {"id": str(row.id), "revision": row.revision}


class MaterialIn(BaseModel):
    code: str
    description: str
    unit: str
    category: str = "material"
    project_id: uuid.UUID | None = None


@router.post("/library/materials", status_code=201)
def add_material(body: MaterialIn, s: DB):
    row = lib.add_material(s, body.code, body.description, body.unit, body.category, body.project_id)
    return {"id": str(row.id)}


class LaborRateIn(BaseModel):
    labor_class: str
    hourly_rate: Decimal
    currency: str
    valid_from: date
    project_id: uuid.UUID | None = None


@router.post("/library/labor-rates", status_code=201)
def add_labor_rate(body: LaborRateIn, s: DB):
    row = lib.add_labor_rate(s, body.labor_class, body.hourly_rate, body.currency, body.valid_from, body.project_id)
    return {"id": str(row.id)}


class CrewIn(BaseModel):
    code: str
    name: str
    members: list[tuple[str, Decimal]]
    project_id: uuid.UUID | None = None


@router.post("/library/crews", status_code=201)
def add_crew(body: CrewIn, s: DB):
    row = lib.add_crew(s, body.code, body.name, body.members, body.project_id)
    return {"id": str(row.id), "revision": row.revision}


class ProductivityIn(BaseModel):
    code: str
    output_per_crew_day: Decimal
    unit: str
    condition: str = "normal"
    project_id: uuid.UUID | None = None


@router.post("/library/productivity", status_code=201)
def add_productivity(body: ProductivityIn, s: DB):
    row = lib.add_productivity(s, body.code, body.output_per_crew_day, body.unit, body.condition, body.project_id)
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
def add_equipment_rate(body: EquipmentRateIn, s: DB):
    row = lib.add_equipment_rate(s, body.equipment_code, body.rental_per_hour, body.currency, body.valid_from,
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
def set_cost_parameters(body: CostParamsIn, s: DB):
    row = lib.set_cost_parameters(s, body.currency, body.hours_per_day, [mk.model_dump() for mk in body.markups],
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


@router.post("/library/assemblies", status_code=201)
def save_assembly(body: AssemblyIn, s: DB, x_cep_user: Actor = None):
    try:
        a = Assembly(
            code=body.code, description=body.description, trade=body.trade, unit=body.unit,
            materials=tuple(MaterialComponent(**{**x, "consumption": Decimal(str(x["consumption"])),
                                                 "wastage_pct": Decimal(str(x.get("wastage_pct", 0)))}) for x in body.materials),
            labor=tuple(LaborComponent(**x) for x in body.labor),
            equipment=tuple(EquipmentComponent(**{**x, **({"hours_per_unit": Decimal(str(x["hours_per_unit"]))} if x.get("hours_per_unit") is not None else {})})
                            for x in body.equipment),
        )
        rev = lib.save_assembly(s, a, body.project_id, body.notes, _actor(x_cep_user))
    except (TypeError, *ENGINE_ERRORS) as exc:
        raise _fail(exc)
    return {"id": str(rev.id), "revision": rev.revision}


# ---------------------------------------------------------------- projects and estimates


class ProjectIn(BaseModel):
    code: str
    name: str
    currency: str = Field(min_length=3, max_length=3)
    unit_system: str = "metric"


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, s: DB):
    p = est.create_project(s, body.code, body.name, body.currency, body.unit_system)
    return {"id": str(p.id), "code": p.code}


@router.get("/projects")
def list_projects(s: DB):
    return [{"id": str(p.id), "code": p.code, "name": p.name, "currency": p.currency}
            for p in s.scalars(select(m.Project).order_by(m.Project.code))]


class EstimateIn(BaseModel):
    code: str
    name: str
    pricing_date: date


@router.post("/projects/{project_id}/estimates", status_code=201)
def create_estimate(project_id: uuid.UUID, body: EstimateIn, s: DB):
    _project(s, project_id)
    v = est.create_estimate(s, project_id, body.code, body.name, body.pricing_date)
    return {"estimate_id": str(v.estimate_id), "version_id": str(v.id), "version_no": v.version_no}


class ItemIn(BaseModel):
    item_key: str
    module: str
    item_type: str
    inputs: dict[str, dict]  # {"length": {"value": "6", "unit": "m"}}
    description: str | None = None
    condition: str = "normal"
    assembly_code: str | None = None


def _item_out(it: m.EstimateItemRecord) -> dict:
    return {"item_key": it.item_key, "module": it.module, "trade": it.trade, "description": it.description,
            "quantity": it.breakdown["quantity"], "unit": it.unit, "unit_rate": it.breakdown["totals"]["unit_rate"],
            "amount": it.breakdown["totals"]["total"], "breakdown": it.breakdown}


@router.post("/projects/{project_id}/versions/{version_id}/items")
def calculate_item(project_id: uuid.UUID, version_id: uuid.UUID, body: ItemIn, s: DB, x_cep_user: Actor = None):
    _version(s, project_id, version_id)
    try:
        it = est.calculate_item(s, version_id, project_id=project_id, actor=_actor(x_cep_user), **body.model_dump())
    except ENGINE_ERRORS as exc:
        raise _fail(exc)
    return _item_out(it)


@router.get("/projects/{project_id}/versions/{version_id}/items")
def list_items(project_id: uuid.UUID, version_id: uuid.UUID, s: DB):
    _version(s, project_id, version_id)
    return [_item_out(i) for i in est.items(s, version_id)]


@router.get("/projects/{project_id}/versions/{version_id}/boq")
def boq(project_id: uuid.UUID, version_id: uuid.UUID, s: DB):
    _version(s, project_id, version_id)
    return jsonable(est.boq(s, version_id))


@router.get("/projects/{project_id}/versions/{version_id}/summary")
def summary(project_id: uuid.UUID, version_id: uuid.UUID, s: DB):
    v = _version(s, project_id, version_id)
    return jsonable({
        "status": v.status,
        "totals": est.cost_summary(s, version_id),
        "materials": est.resource_summary(s, version_id, "material"),
        "labor": est.resource_summary(s, version_id, "labor"),
        "equipment": est.resource_summary(s, version_id, "equipment"),
    })


@router.post("/projects/{project_id}/versions/{version_id}/approve")
def approve(project_id: uuid.UUID, version_id: uuid.UUID, s: DB, x_cep_user: Actor = None):
    _version(s, project_id, version_id)
    try:
        v = est.approve(s, version_id, _actor(x_cep_user))
    except ENGINE_ERRORS as exc:
        raise _fail(exc)
    return {"version_id": str(v.id), "status": v.status, "total_amount": str(v.total_amount)}


class RevisionIn(BaseModel):
    pricing_date: date | None = None


@router.post("/projects/{project_id}/versions/{version_id}/revisions", status_code=201)
def new_revision(project_id: uuid.UUID, version_id: uuid.UUID, body: RevisionIn, s: DB, x_cep_user: Actor = None):
    _version(s, project_id, version_id)
    try:
        v = est.new_revision(s, version_id, body.pricing_date, _actor(x_cep_user))
    except ENGINE_ERRORS as exc:
        raise _fail(exc)
    return {"version_id": str(v.id), "version_no": v.version_no}


@router.get("/projects/{project_id}/compare")
def compare(project_id: uuid.UUID, old: uuid.UUID, new: uuid.UUID, s: DB):
    _version(s, project_id, old)
    _version(s, project_id, new)
    return jsonable(est.compare_versions(s, old, new))
