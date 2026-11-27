"""Estimate service: calculate items through the universal engine, persist them with full
traceability, approve immutable versions, derive revisions, compare and report."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..engine.estimate import compare_snapshots
from ..engine.service import EstimatingEngine
from .audit import audit
from .ratebook import SqlAssemblyResolver, SqlFormulaResolver, SqlRateBook


class EstimateLocked(RuntimeError):
    pass


def create_project(s: Session, code: str, name: str, currency: str, unit_system: str = "metric") -> m.Project:
    p = m.Project(code=code, name=name, currency=currency, unit_system=unit_system)
    s.add(p)
    s.flush()
    audit(s, p.id, "projects", p.id, "create", "system", {"code": code})
    return p


def create_estimate(s: Session, project_id: uuid.UUID, code: str, name: str, pricing_date: date) -> m.EstimateVersion:
    e = m.EstimateRecord(project_id=project_id, code=code, name=name)
    s.add(e)
    s.flush()
    v = m.EstimateVersion(project_id=project_id, estimate_id=e.id, version_no=1, status="draft", pricing_date=pricing_date)
    s.add(v)
    s.flush()
    return v


def _version(s: Session, version_id: uuid.UUID, project_id: uuid.UUID | None = None) -> m.EstimateVersion:
    v = s.get(m.EstimateVersion, version_id)
    if v is None or (project_id is not None and v.project_id != project_id):
        raise LookupError("estimate version not found")
    return v


def _raw_inputs(inputs: dict) -> dict:
    out = {}
    for k, v in inputs.items():
        out[k] = (str(v["value"]), v["unit"]) if isinstance(v, dict) else str(v)
    return out


def calculate_item(
    s: Session,
    version_id: uuid.UUID,
    *,
    item_key: str,
    module: str,
    item_type: str,
    inputs: dict,
    description: str | None = None,
    condition: str = "normal",
    assembly_code: str | None = None,
    building_id=None,
    floor_id=None,
    zone_id=None,
    room_id=None,
    source: str = "manual",
    drawing_reference_id=None,
    bim_element_id=None,
    actor: str = "system",
    project_id: uuid.UUID | None = None,
) -> m.EstimateItemRecord:
    """Calculate (or recalculate) one estimate item. Same item_key → same row, never a duplicate."""
    v = _version(s, version_id, project_id)
    if v.status != "draft":
        raise EstimateLocked(f"estimate version {v.version_no} is {v.status}; create a new revision to change it")

    formulas = SqlFormulaResolver(s, v.project_id)
    assemblies = SqlAssemblyResolver(s, v.project_id)
    engine = EstimatingEngine(formulas=formulas, assemblies=assemblies, rates=SqlRateBook(s, v.project_id))
    result = engine.calculate_item(
        item_id=item_key, module=module, item_type=item_type, inputs=_raw_inputs(inputs), as_of=v.pricing_date,
        description=description, condition=condition, assembly_code=assembly_code,
    )
    b = result.breakdown
    fs = result.quantity_source
    formula_version_id = formulas.used[(fs.formula_code, fs.formula_version)]
    assembly_revision_id = assemblies.used[result.assembly_code]
    places = engine.rates.parameters().money_places

    item = s.scalars(
        select(m.EstimateItemRecord).where(
            m.EstimateItemRecord.estimate_version_id == v.id, m.EstimateItemRecord.item_key == item_key
        )
    ).first()
    action = "recalculate" if item else "create"
    if item is None:
        item = m.EstimateItemRecord(project_id=v.project_id, estimate_version_id=v.id, item_key=item_key)
        s.add(item)
    else:
        s.execute(delete(m.CostComponent).where(m.CostComponent.estimate_item_id == item.id))
        s.execute(delete(m.QuantityTakeoff).where(m.QuantityTakeoff.estimate_item_id == item.id))
    item.module, item.item_type, item.trade = result.module, item_type, result.trade
    item.description = result.description
    item.building_id, item.floor_id, item.zone_id, item.room_id = building_id, floor_id, zone_id, room_id
    item.assembly_revision_id, item.formula_version_id = assembly_revision_id, formula_version_id
    item.condition, item.inputs = condition, inputs
    item.quantity, item.unit = b.quantity, b.unit
    item.amount, item.unit_rate = b.total_amount, b.unit_rate
    item.breakdown = b.to_dict()
    item.updated_at = datetime.now(timezone.utc)
    s.flush()

    comps = []
    for x in b.materials:
        comps.append(m.CostComponent(project_id=v.project_id, estimate_item_id=item.id, category=x.category, code=x.item_code,
                                     description=x.description, quantity=x.priced_quantity, unit=x.price_unit,
                                     unit_cost=x.unit_price, amount=x.amount, source_ref=x.price_source))
    for l in b.labor:
        for mm in l.members:
            comps.append(m.CostComponent(project_id=v.project_id, estimate_item_id=item.id, category="labor", code=mm.labor_class,
                                         description=l.task, quantity=mm.hours, unit="hr", unit_cost=mm.hourly_rate,
                                         amount=mm.amount, source_ref=mm.rate_source))
    for e in b.equipment:
        comps.append(m.CostComponent(project_id=v.project_id, estimate_item_id=item.id, category="equipment",
                                     code=e.equipment_code, description=e.description, quantity=e.hours, unit="hr",
                                     unit_cost=(e.amount / e.hours) if e.hours else Decimal(0), amount=e.amount,
                                     source_ref=e.rate_source))
    for k in b.markups:
        comps.append(m.CostComponent(project_id=v.project_id, estimate_item_id=item.id, category="markup", code=k.code,
                                     description=k.label, quantity=k.pct, unit="%", unit_cost=k.base_amount,
                                     amount=k.amount, source_ref=engine.rates.parameters().source_id))
    s.add_all(comps)
    take = m.QuantityTakeoff(project_id=v.project_id, estimate_item_id=item.id, formula_version_id=formula_version_id,
                             source=source, drawing_reference_id=drawing_reference_id, bim_element_id=bim_element_id,
                             methodology=fs.methodology, value=fs.value, unit=fs.unit,
                             dimensions=[m.QuantityDimension(name=i.name, value=i.value, unit=i.unit, si_value=i.si) for i in fs.inputs])
    s.add(take)
    s.flush()
    audit(s, v.project_id, "estimate_items", item.id, action, actor,
          {"item_key": item_key, "quantity": str(b.quantity), "amount": str(round(b.total_amount, places))})
    return item


def remove_item(s: Session, version_id: uuid.UUID, item_key: str, actor: str = "system") -> None:
    v = _version(s, version_id)
    if v.status != "draft":
        raise EstimateLocked(f"estimate version {v.version_no} is {v.status}")
    item = s.scalars(select(m.EstimateItemRecord).where(m.EstimateItemRecord.estimate_version_id == v.id,
                                                       m.EstimateItemRecord.item_key == item_key)).first()
    if item:
        s.delete(item)
        s.flush()
        audit(s, v.project_id, "estimate_items", item.id, "delete", actor, {"item_key": item_key})


def items(s: Session, version_id: uuid.UUID) -> list[m.EstimateItemRecord]:
    return list(s.scalars(select(m.EstimateItemRecord).where(m.EstimateItemRecord.estimate_version_id == version_id)
                          .order_by(m.EstimateItemRecord.item_key)))


def boq(s: Session, version_id: uuid.UUID) -> list[dict]:
    rows = s.execute(
        select(
            m.EstimateItemRecord.trade,
            m.AssemblyRecord.code,
            m.AssemblyRecord.description,
            m.EstimateItemRecord.unit,
            func.sum(m.EstimateItemRecord.quantity),
            func.sum(m.EstimateItemRecord.amount),
            func.array_agg(m.EstimateItemRecord.item_key),
        )
        .join(m.AssemblyRevision, m.AssemblyRevision.id == m.EstimateItemRecord.assembly_revision_id)
        .join(m.AssemblyRecord, m.AssemblyRecord.id == m.AssemblyRevision.assembly_id)
        .where(m.EstimateItemRecord.estimate_version_id == version_id)
        .group_by(m.EstimateItemRecord.trade, m.AssemblyRecord.code, m.AssemblyRecord.description, m.EstimateItemRecord.unit)
        .order_by(m.EstimateItemRecord.trade, m.AssemblyRecord.code)
    ).all()
    return [
        {"line_no": n, "trade": t, "assembly_code": c, "description": d, "unit": u, "quantity": q, "amount": a,
         "unit_rate": (a / q) if q else Decimal(0), "source_item_keys": sorted(keys)}
        for n, (t, c, d, u, q, a, keys) in enumerate(rows, start=1)
    ]


def cost_summary(s: Session, version_id: uuid.UUID) -> dict:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for it in items(s, version_id):
        for k, val in it.breakdown["totals"].items():
            if k not in ("unit_rate", "direct_unit_cost"):
                totals[k] += Decimal(val)
    return dict(totals)


def resource_summary(s: Session, version_id: uuid.UUID, category: str) -> list[dict]:
    rows = s.execute(
        select(m.CostComponent.code, m.CostComponent.unit, func.sum(m.CostComponent.quantity), func.sum(m.CostComponent.amount))
        .join(m.EstimateItemRecord, m.EstimateItemRecord.id == m.CostComponent.estimate_item_id)
        .where(m.EstimateItemRecord.estimate_version_id == version_id, m.CostComponent.category == category)
        .group_by(m.CostComponent.code, m.CostComponent.unit)
        .order_by(m.CostComponent.code)
    ).all()
    return [{"code": c, "unit": u, "quantity": q, "amount": a} for c, u, q, a in rows]


def approve(s: Session, version_id: uuid.UUID, actor: str, note: str = "") -> m.EstimateVersion:
    v = _version(s, version_id)
    if v.status != "draft":
        raise EstimateLocked(f"estimate version {v.version_no} is already {v.status}")
    lines = boq(s, version_id)
    if not lines:
        raise ValueError("cannot approve an empty estimate")
    for line in lines:  # materialise the BOQ while still draft, then freeze
        s.add(m.BoqItem(project_id=v.project_id, estimate_version_id=v.id, line_no=line["line_no"], trade=line["trade"],
                        assembly_code=line["assembly_code"], description=line["description"], unit=line["unit"],
                        quantity=line["quantity"], unit_rate=line["unit_rate"], amount=line["amount"],
                        source_item_keys=line["source_item_keys"]))
    s.flush()
    previous = s.scalars(select(m.EstimateVersion).where(m.EstimateVersion.estimate_id == v.estimate_id,
                                                        m.EstimateVersion.status == "approved")).all()
    for p in previous:
        p.status = "superseded"
    v.total_amount = sum((line["amount"] for line in lines), Decimal(0))
    v.status = "approved"
    v.approved_at = datetime.now(timezone.utc)
    v.approved_by = actor
    s.add(m.Approval(project_id=v.project_id, entity="estimate_versions", entity_id=v.id, decision="approved", actor=actor, note=note))
    s.flush()
    audit(s, v.project_id, "estimate_versions", v.id, "approve", actor, {"total": str(v.total_amount)})
    return v


def new_revision(s: Session, version_id: uuid.UUID, pricing_date: date | None = None, actor: str = "system") -> m.EstimateVersion:
    """Start a draft revision from any version. Items are recalculated through the engine at the new
    pricing date; the source version is untouched."""
    src = _version(s, version_id)
    next_no = (s.scalar(select(func.max(m.EstimateVersion.version_no)).where(m.EstimateVersion.estimate_id == src.estimate_id)) or 0) + 1
    v = m.EstimateVersion(project_id=src.project_id, estimate_id=src.estimate_id, version_no=next_no, status="draft",
                          pricing_date=pricing_date or src.pricing_date, parent_version_id=src.id)
    s.add(v)
    s.flush()
    for it in items(s, src.id):
        asm = s.get(m.AssemblyRevision, it.assembly_revision_id)
        rec = s.get(m.AssemblyRecord, asm.assembly_id)
        takeoff = s.scalars(select(m.QuantityTakeoff).where(m.QuantityTakeoff.estimate_item_id == it.id)).first()
        calculate_item(s, v.id, item_key=it.item_key, module=it.module, item_type=it.item_type, inputs=it.inputs,
                       description=it.description, condition=it.condition, assembly_code=rec.code,
                       building_id=it.building_id, floor_id=it.floor_id, zone_id=it.zone_id, room_id=it.room_id,
                       source=takeoff.source if takeoff else "manual",
                       drawing_reference_id=takeoff.drawing_reference_id if takeoff else None,
                       bim_element_id=takeoff.bim_element_id if takeoff else None, actor=actor)
    audit(s, v.project_id, "estimate_versions", v.id, "revise", actor, {"from": str(src.id)})
    return v


def snapshot(s: Session, version_id: uuid.UUID) -> dict[str, dict]:
    return {
        it.item_key: {
            "description": it.description,
            "module": it.module,
            "trade": it.trade,
            "quantity": it.breakdown["quantity"],
            "unit": it.unit,
            "amount": it.breakdown["totals"]["total"],
            "unit_rate": it.breakdown["totals"]["unit_rate"],
        }
        for it in items(s, version_id)
    }


def compare_versions(s: Session, old_version_id: uuid.UUID, new_version_id: uuid.UUID) -> dict:
    a, b = _version(s, old_version_id), _version(s, new_version_id)
    if a.project_id != b.project_id:
        raise LookupError("versions belong to different projects")
    return compare_snapshots(snapshot(s, a.id), snapshot(s, b.id))
