"""PostgreSQL-backed RateBook, formula resolver and assembly resolver for one project.

Resolution rule (same for every rate type): if the project has its own
records for a code, only those are used; otherwise the master library
(project_id IS NULL) applies. Records are then chosen by validity date and
highest revision. Another project's records are never visible.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..engine.assembly import Assembly, EquipmentComponent, LaborComponent, MaterialComponent
from ..engine.formula.evaluator import FormulaDefinition, InputSpec
from ..engine.rates import (
    CostParameters,
    Crew,
    CrewMember as EngineCrewMember,
    EquipmentRate as EngineEquipmentRate,
    LaborRate as EngineLaborRate,
    Markup,
    MaterialPrice as EnginePrice,
    Productivity,
    RateNotFound,
    pick_price,
    resolve_productivity,
)
from ..db import models as m


def _scoped(rows: list, project_id: uuid.UUID) -> list:
    own = [r for r in rows if r.project_id == project_id]
    return own if own else [r for r in rows if r.project_id is None]


def _visible(column, project_id: uuid.UUID):
    return or_(column == project_id, column.is_(None))


def _valid(row, as_of: date) -> bool:
    return row.valid_from <= as_of and (row.valid_to is None or as_of <= row.valid_to)


class SqlRateBook:
    def __init__(self, session: Session, project_id: uuid.UUID):
        self.s = session
        self.project_id = project_id
        self._params: CostParameters | None = None

    def material_price(self, item_code, as_of, supplier=None, brand=None):
        rows = self.s.scalars(
            select(m.MaterialPrice).where(
                m.MaterialPrice.material_code == item_code, _visible(m.MaterialPrice.project_id, self.project_id)
            )
        ).all()
        rows = _scoped(list(rows), self.project_id)
        suppliers = {}
        if rows:
            ids = {r.supplier_id for r in rows if r.supplier_id}
            if ids:
                suppliers = {s.id: s.code for s in self.s.scalars(select(m.Supplier).where(m.Supplier.id.in_(ids)))}
        cats = {
            mat.code: mat.category
            for mat in self.s.scalars(select(m.Material).where(m.Material.code == item_code))
        }
        candidates = [
            EnginePrice(
                item_code=r.material_code,
                unit=r.unit,
                price=r.price,
                currency=r.currency,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                supplier=suppliers.get(r.supplier_id),
                brand=r.brand,
                category=cats.get(item_code, "material"),
                revision=r.revision,
                source_id=f"material_prices:{r.id}",
            )
            for r in rows
        ]
        return pick_price(candidates, item_code, as_of, supplier, brand)

    def labor_rate(self, labor_class, as_of):
        rows = self.s.scalars(
            select(m.LaborRate).where(
                m.LaborRate.labor_class_code == labor_class, _visible(m.LaborRate.project_id, self.project_id)
            )
        ).all()
        rows = [r for r in _scoped(list(rows), self.project_id) if _valid(r, as_of)]
        if not rows:
            raise RateNotFound(f"no labor rate for '{labor_class}' valid on {as_of.isoformat()}")
        r = max(rows, key=lambda r: (r.valid_from, r.revision))
        return EngineLaborRate(r.labor_class_code, r.hourly_rate, r.currency, r.valid_from, r.valid_to, f"labor_rates:{r.id}")

    def crew(self, code):
        rows = self.s.scalars(
            select(m.CrewComposition)
            .options(selectinload(m.CrewComposition.members))
            .where(m.CrewComposition.code == code, _visible(m.CrewComposition.project_id, self.project_id))
        ).all()
        rows = _scoped(list(rows), self.project_id)
        if not rows:
            raise RateNotFound(f"no crew '{code}'")
        c = max(rows, key=lambda c: c.revision)
        return Crew(
            c.code,
            c.name,
            tuple(EngineCrewMember(mm.labor_class_code, mm.count) for mm in c.members),
            f"crew_compositions:{c.id}",
        )

    def productivity(self, code, condition):
        rows = self.s.scalars(
            select(m.ProductivityRate).where(
                m.ProductivityRate.code == code, _visible(m.ProductivityRate.project_id, self.project_id)
            )
        ).all()
        rows = sorted(_scoped(list(rows), self.project_id), key=lambda r: r.revision)
        records = [
            Productivity(r.code, r.condition, r.output_per_crew_day, r.unit, f"productivity_rates:{r.id}") for r in rows
        ]
        return resolve_productivity(records, self.parameters().productivity_factors, code, condition)

    def equipment_rate(self, equipment_code, as_of):
        rows = self.s.scalars(
            select(m.EquipmentRate).where(
                m.EquipmentRate.equipment_code == equipment_code, _visible(m.EquipmentRate.project_id, self.project_id)
            )
        ).all()
        rows = [r for r in _scoped(list(rows), self.project_id) if _valid(r, as_of)]
        if not rows:
            raise RateNotFound(f"no equipment rate for '{equipment_code}' valid on {as_of.isoformat()}")
        r = max(rows, key=lambda r: (r.valid_from, r.revision))
        return EngineEquipmentRate(
            r.equipment_code,
            r.rental_per_hour,
            r.currency,
            r.valid_from,
            r.valid_to,
            r.fuel_litres_per_hour,
            r.fuel_item_code,
            r.operating_per_hour,
            f"equipment_rates:{r.id}",
        )

    def parameters(self):
        if self._params is None:
            rows = self.s.scalars(
                select(m.CostParameterSet).where(_visible(m.CostParameterSet.project_id, self.project_id))
            ).all()
            rows = _scoped(list(rows), self.project_id)
            if not rows:
                raise RateNotFound("no cost parameters configured for this project or the master library")
            p = max(rows, key=lambda r: r.revision)
            self._params = CostParameters(
                currency=p.currency,
                hours_per_day=p.hours_per_day,
                markups=tuple(Markup(x["code"], x.get("label", x["code"]), Decimal(str(x["pct"])), x.get("basis", "direct")) for x in p.markups),
                productivity_factors={k: Decimal(str(v)) for k, v in (p.productivity_factors or {}).items()},
                money_places=p.money_places,
                quantity_places=p.quantity_places,
                source_id=f"cost_parameters:{p.id}",
            )
        return self._params


# ---------------------------------------------------------------- formulas


def formula_from_row(f: m.Formula, v: m.FormulaVersion) -> FormulaDefinition:
    inputs = tuple(
        InputSpec(
            name=i["name"],
            kind=i["kind"],
            unit=i["unit"],
            label=i.get("label", ""),
            required=i.get("required", True),
            default=None if i.get("default") is None else Decimal(i["default"]),
            min_value=None if i.get("min_value") is None else Decimal(i["min_value"]),
            max_value=None if i.get("max_value") is None else Decimal(i["max_value"]),
        )
        for i in v.inputs
    )
    return FormulaDefinition(f.code, f.name, v.expression, v.output_kind, v.output_unit, inputs, v.version, f.description).validate()


class SqlFormulaResolver:
    def __init__(self, session: Session, project_id: uuid.UUID | None):
        self.s = session
        self.project_id = project_id
        self.used: dict[tuple[str, int], uuid.UUID] = {}

    def formula_row(self, code: str) -> m.Formula:
        cond = m.Formula.project_id.is_(None) if self.project_id is None else _visible(m.Formula.project_id, self.project_id)
        rows = self.s.scalars(select(m.Formula).where(m.Formula.code == code, cond)).all()
        rows = _scoped(list(rows), self.project_id) if self.project_id else list(rows)
        if not rows:
            raise KeyError(f"unknown formula '{code}'")
        return rows[0]

    def __call__(self, code: str, version: int | None = None) -> FormulaDefinition:
        f = self.formula_row(code)
        q = select(m.FormulaVersion).where(m.FormulaVersion.formula_id == f.id)
        q = q.where(m.FormulaVersion.version == version) if version else q.order_by(m.FormulaVersion.version.desc()).limit(1)
        v = self.s.scalars(q).first()
        if v is None:
            raise KeyError(f"formula '{code}' has no version {version}")
        self.used[(code, v.version)] = v.id
        return formula_from_row(f, v)


# ---------------------------------------------------------------- assemblies


def assembly_from_row(a: m.AssemblyRecord, r: m.AssemblyRevision) -> Assembly:
    mats, labor, equip = [], [], []
    for c in r.components:
        if c.kind == "material":
            mats.append(MaterialComponent(c.item_code, c.description, c.consumption, c.unit, c.wastage_pct or Decimal(0),
                                          c.supplier_code, c.brand, c.category or "material"))
        elif c.kind == "labor":
            labor.append(LaborComponent(c.task, c.crew_code, c.productivity_code))
        else:
            equip.append(EquipmentComponent(c.equipment_code, c.description, c.basis, c.hours_per_unit, c.labor_task,
                                            c.count or Decimal(1)))
    return Assembly(
        code=a.code,
        description=a.description,
        trade=a.trade,
        unit=a.unit,
        revision=r.revision,
        materials=tuple(mats),
        labor=tuple(labor),
        equipment=tuple(equip),
        project_id=str(a.project_id) if a.project_id else None,
        notes=r.notes,
    )


class SqlAssemblyResolver:
    def __init__(self, session: Session, project_id: uuid.UUID):
        self.s = session
        self.project_id = project_id
        self.used: dict[str, uuid.UUID] = {}

    def revision_row(self, code: str) -> tuple[m.AssemblyRecord, m.AssemblyRevision]:
        rows = self.s.scalars(
            select(m.AssemblyRecord).where(m.AssemblyRecord.code == code, _visible(m.AssemblyRecord.project_id, self.project_id))
        ).all()
        rows = _scoped(list(rows), self.project_id)
        if not rows:
            raise KeyError(f"unknown assembly '{code}'")
        a = rows[0]
        r = self.s.scalars(
            select(m.AssemblyRevision)
            .options(selectinload(m.AssemblyRevision.components))
            .where(m.AssemblyRevision.assembly_id == a.id)
            .order_by(m.AssemblyRevision.revision.desc())
            .limit(1)
        ).first()
        if r is None:
            raise KeyError(f"assembly '{code}' has no revisions")
        return a, r

    def __call__(self, code: str) -> Assembly:
        a, r = self.revision_row(code)
        self.used[code] = r.id
        return assembly_from_row(a, r)
