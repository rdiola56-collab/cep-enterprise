"""Write-side services for the shared libraries: formulas, assemblies, prices and rates.

Every change is a new row (version or revision); nothing is updated in place.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..engine.assembly import Assembly
from ..engine.formula.evaluator import FormulaDefinition, FormulaDefinitionError
from .audit import audit
from .ratebook import SqlAssemblyResolver, SqlFormulaResolver


# ---------------------------------------------------------------- formulas


def publish_formula(s: Session, definition: FormulaDefinition, project_id: uuid.UUID | None = None, actor: str = "system") -> m.FormulaVersion:
    """Create a formula, or a new version of it. Rejects invalid or unchanged formulas."""
    f = s.scalars(
        select(m.Formula).where(
            m.Formula.code == definition.code,
            m.Formula.project_id.is_(None) if project_id is None else m.Formula.project_id == project_id,
        )
    ).first()
    if f is None:
        f = m.Formula(project_id=project_id, code=definition.code, name=definition.name, description=definition.description)
        s.add(f)
        s.flush()
        last = None
    else:
        last = s.scalars(
            select(m.FormulaVersion).where(m.FormulaVersion.formula_id == f.id).order_by(m.FormulaVersion.version.desc()).limit(1)
        ).first()
    version = (last.version + 1) if last else 1
    validated = replace(definition, version=version, _ast=None).validate()
    if last and last.content_hash == validated.content_hash:
        raise FormulaDefinitionError(f"formula '{definition.code}' is unchanged from version {last.version}")
    v = m.FormulaVersion(
        formula_id=f.id,
        version=version,
        expression=definition.expression,
        output_kind=definition.output_kind,
        output_unit=definition.output_unit,
        inputs=[i.to_dict() for i in definition.inputs],
        content_hash=validated.content_hash,
        created_by=actor,
    )
    s.add(v)
    s.flush()
    audit(s, project_id, "formula_versions", v.id, "publish", actor, {"code": definition.code, "version": version})
    return v


# ---------------------------------------------------------------- assemblies


def _components(rev_id: uuid.UUID, a: Assembly) -> list[m.AssemblyComponent]:
    out, n = [], 0
    for c in a.materials:
        out.append(m.AssemblyComponent(assembly_revision_id=rev_id, kind="material", sort=n, description=c.description,
                                       item_code=c.item_code, consumption=c.consumption, unit=c.unit,
                                       wastage_pct=c.wastage_pct, category=c.category, supplier_code=c.supplier, brand=c.brand))
        n += 1
    for c in a.labor:
        out.append(m.AssemblyComponent(assembly_revision_id=rev_id, kind="labor", sort=n, description=c.task, task=c.task,
                                       crew_code=c.crew_code, productivity_code=c.productivity_code))
        n += 1
    for c in a.equipment:
        out.append(m.AssemblyComponent(assembly_revision_id=rev_id, kind="equipment", sort=n, description=c.description,
                                       equipment_code=c.equipment_code, basis=c.basis, hours_per_unit=c.hours_per_unit,
                                       labor_task=c.labor_task, count=c.count))
        n += 1
    return out


def save_assembly(s: Session, a: Assembly, project_id: uuid.UUID | None = None, notes: str = "", actor: str = "system",
                  parent_revision_id: uuid.UUID | None = None) -> m.AssemblyRevision:
    """Create an assembly or add a new revision to it (in the master library or a project)."""
    a.validate()
    rec = s.scalars(
        select(m.AssemblyRecord).where(
            m.AssemblyRecord.code == a.code,
            m.AssemblyRecord.project_id.is_(None) if project_id is None else m.AssemblyRecord.project_id == project_id,
        )
    ).first()
    if rec is None:
        rec = m.AssemblyRecord(project_id=project_id, code=a.code, description=a.description, trade=a.trade, unit=a.unit)
        s.add(rec)
        s.flush()
    elif rec.unit != a.unit:
        raise ValueError(f"assembly '{a.code}' is measured in {rec.unit}; a revision cannot change it to {a.unit}")
    last = s.scalar(select(func.max(m.AssemblyRevision.revision)).where(m.AssemblyRevision.assembly_id == rec.id)) or 0
    rev = m.AssemblyRevision(assembly_id=rec.id, revision=last + 1, notes=notes, parent_revision_id=parent_revision_id)
    s.add(rev)
    s.flush()
    s.add_all(_components(rev.id, a))
    s.flush()
    audit(s, project_id, "assembly_revisions", rev.id, "create", actor, {"code": a.code, "revision": rev.revision})
    return rev


def customize_assembly_for_project(s: Session, code: str, project_id: uuid.UUID, actor: str = "system", **changes) -> m.AssemblyRevision:
    """Copy the assembly in effect for the project, apply changes, and save it as a project revision."""
    resolver = SqlAssemblyResolver(s, project_id)
    _, base_rev = resolver.revision_row(code)
    base = resolver(code)
    custom = replace(base, **changes)
    return save_assembly(s, custom, project_id, notes=f"customised from revision {base_rev.id}", actor=actor,
                         parent_revision_id=base_rev.id)


# ---------------------------------------------------------------- prices and rates


def add_material(s: Session, code: str, description: str, unit: str, category: str = "material", project_id=None) -> m.Material:
    mat = m.Material(project_id=project_id, code=code, description=description, unit=unit, category=category)
    s.add(mat)
    s.flush()
    return mat


def add_material_price(s: Session, code: str, unit: str, price: Decimal | str, currency: str, valid_from: date,
                       project_id: uuid.UUID | None = None, supplier_id=None, brand=None, valid_to=None,
                       actor: str = "system") -> m.MaterialPrice:
    last = s.scalar(
        select(func.max(m.MaterialPrice.revision)).where(
            m.MaterialPrice.material_code == code,
            m.MaterialPrice.project_id.is_(None) if project_id is None else m.MaterialPrice.project_id == project_id,
            m.MaterialPrice.valid_from == valid_from,
        )
    ) or 0
    row = m.MaterialPrice(project_id=project_id, material_code=code, unit=unit, price=Decimal(str(price)), currency=currency,
                          valid_from=valid_from, valid_to=valid_to, supplier_id=supplier_id, brand=brand, revision=last + 1)
    s.add(row)
    s.flush()
    audit(s, project_id, "material_prices", row.id, "create", actor, {"code": code, "price": str(price), "revision": last + 1})
    return row


def add_labor_rate(s: Session, labor_class: str, hourly: Decimal | str, currency: str, valid_from: date, project_id=None) -> m.LaborRate:
    row = m.LaborRate(project_id=project_id, labor_class_code=labor_class, hourly_rate=Decimal(str(hourly)), currency=currency,
                      valid_from=valid_from)
    s.add(row)
    s.flush()
    return row


def add_crew(s: Session, code: str, name: str, members: list[tuple[str, Decimal | int]], project_id=None) -> m.CrewComposition:
    last = s.scalar(
        select(func.max(m.CrewComposition.revision)).where(
            m.CrewComposition.code == code,
            m.CrewComposition.project_id.is_(None) if project_id is None else m.CrewComposition.project_id == project_id,
        )
    ) or 0
    crew = m.CrewComposition(project_id=project_id, code=code, name=name, revision=last + 1,
                             members=[m.CrewMember(labor_class_code=c, count=Decimal(str(n)), sort=i) for i, (c, n) in enumerate(members)])
    s.add(crew)
    s.flush()
    return crew


def add_productivity(s: Session, code: str, output: Decimal | str, unit: str, condition: str = "normal", project_id=None) -> m.ProductivityRate:
    last = s.scalar(
        select(func.max(m.ProductivityRate.revision)).where(
            m.ProductivityRate.code == code, m.ProductivityRate.condition == condition,
            m.ProductivityRate.project_id.is_(None) if project_id is None else m.ProductivityRate.project_id == project_id,
        )
    ) or 0
    row = m.ProductivityRate(project_id=project_id, code=code, condition=condition, output_per_crew_day=Decimal(str(output)),
                             unit=unit, revision=last + 1)
    s.add(row)
    s.flush()
    return row


def add_equipment_rate(s: Session, code: str, rental: Decimal | str, currency: str, valid_from: date, fuel_lph="0",
                       fuel_item_code=None, operating="0", project_id=None) -> m.EquipmentRate:
    row = m.EquipmentRate(project_id=project_id, equipment_code=code, rental_per_hour=Decimal(str(rental)), currency=currency,
                          valid_from=valid_from, fuel_litres_per_hour=Decimal(str(fuel_lph)), fuel_item_code=fuel_item_code,
                          operating_per_hour=Decimal(str(operating)))
    s.add(row)
    s.flush()
    return row


def set_cost_parameters(s: Session, currency: str, hours_per_day, markups: list[dict], productivity_factors: dict | None = None,
                        project_id=None, money_places: int = 2) -> m.CostParameterSet:
    last = s.scalar(
        select(func.max(m.CostParameterSet.revision)).where(
            m.CostParameterSet.project_id.is_(None) if project_id is None else m.CostParameterSet.project_id == project_id
        )
    ) or 0
    row = m.CostParameterSet(project_id=project_id, revision=last + 1, currency=currency, hours_per_day=Decimal(str(hours_per_day)),
                             markups=[{**mk, "pct": str(mk["pct"])} for mk in markups],
                             productivity_factors={k: str(v) for k, v in (productivity_factors or {}).items()},
                             money_places=money_places)
    s.add(row)
    s.flush()
    return row


__all__ = [
    "SqlFormulaResolver",
    "add_crew",
    "add_equipment_rate",
    "add_labor_rate",
    "add_material",
    "add_material_price",
    "add_productivity",
    "customize_assembly_for_project",
    "publish_formula",
    "save_assembly",
    "set_cost_parameters",
]
