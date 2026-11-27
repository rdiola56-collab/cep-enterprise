"""Material, labor, equipment and rate-analysis engines.

``calculate_item`` is the single entry point every estimating module uses to
turn a work quantity plus an assembly into a fully traced cost breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext

from .assembly import Assembly
from .precision import D, ENGINE_PRECISION, money, quantize
from .rates import CostParameters, RateBook
from .units import Quantity, UnitError, convert, get_unit

HUNDRED = Decimal(100)


class CostingError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialLine:
    item_code: str
    description: str
    category: str
    net_quantity: Decimal
    wastage_pct: Decimal
    gross_quantity: Decimal
    unit: str
    price_unit: str
    priced_quantity: Decimal  # gross quantity expressed in the price unit
    unit_price: Decimal
    amount: Decimal
    supplier: str | None
    brand: str | None
    price_source: str


@dataclass(frozen=True)
class LaborMemberLine:
    labor_class: str
    count: Decimal
    hours: Decimal
    hourly_rate: Decimal
    amount: Decimal
    rate_source: str


@dataclass(frozen=True)
class LaborLine:
    task: str
    crew_code: str
    condition: str
    productivity: Decimal  # output per crew-day, in the assembly unit
    productivity_source: str
    crew_days: Decimal
    crew_size: Decimal
    manpower: Decimal  # workers required to finish within the target duration (crew size if no duration)
    crews_required: Decimal
    labor_hours: Decimal
    amount: Decimal
    members: tuple[LaborMemberLine, ...]


@dataclass(frozen=True)
class EquipmentLine:
    equipment_code: str
    description: str
    basis: str
    hours: Decimal
    rental_cost: Decimal
    fuel_litres: Decimal
    fuel_cost: Decimal
    operating_cost: Decimal
    amount: Decimal
    rate_source: str
    fuel_price_source: str | None


@dataclass(frozen=True)
class MarkupLine:
    code: str
    label: str
    pct: Decimal
    basis: str
    base_amount: Decimal
    amount: Decimal


@dataclass(frozen=True)
class CostBreakdown:
    assembly_ref: str
    quantity: Decimal
    unit: str
    pricing_date: date
    condition: str
    currency: str
    materials: tuple[MaterialLine, ...]
    labor: tuple[LaborLine, ...]
    equipment: tuple[EquipmentLine, ...]
    markups: tuple[MarkupLine, ...]
    totals: dict[str, Decimal] = field(default_factory=dict)

    @property
    def direct_cost(self) -> Decimal:
        return self.totals["direct"]

    @property
    def total_amount(self) -> Decimal:
        return self.totals["total"]

    @property
    def unit_rate(self) -> Decimal:
        return self.totals["unit_rate"]

    def to_dict(self, money_places: int | None = None) -> dict:
        """Serialise. With money_places=None values keep full precision (for snapshots)."""
        fmt = (lambda v: str(v)) if money_places is None else (lambda v: str(money(v, money_places)))
        raw = str

        return {
            "assembly_ref": self.assembly_ref,
            "quantity": raw(self.quantity),
            "unit": self.unit,
            "pricing_date": self.pricing_date.isoformat(),
            "condition": self.condition,
            "currency": self.currency,
            "materials": [
                {
                    "item_code": m.item_code,
                    "description": m.description,
                    "category": m.category,
                    "net_quantity": raw(m.net_quantity),
                    "wastage_pct": raw(m.wastage_pct),
                    "gross_quantity": raw(m.gross_quantity),
                    "unit": m.unit,
                    "price_unit": m.price_unit,
                    "priced_quantity": raw(m.priced_quantity),
                    "unit_price": raw(m.unit_price),
                    "amount": fmt(m.amount),
                    "supplier": m.supplier,
                    "brand": m.brand,
                    "price_source": m.price_source,
                }
                for m in self.materials
            ],
            "labor": [
                {
                    "task": l.task,
                    "crew_code": l.crew_code,
                    "condition": l.condition,
                    "productivity": raw(l.productivity),
                    "productivity_source": l.productivity_source,
                    "crew_days": raw(l.crew_days),
                    "crew_size": raw(l.crew_size),
                    "crews_required": raw(l.crews_required),
                    "manpower": raw(l.manpower),
                    "labor_hours": raw(l.labor_hours),
                    "amount": fmt(l.amount),
                    "members": [
                        {
                            "labor_class": m.labor_class,
                            "count": raw(m.count),
                            "hours": raw(m.hours),
                            "hourly_rate": raw(m.hourly_rate),
                            "amount": fmt(m.amount),
                            "rate_source": m.rate_source,
                        }
                        for m in l.members
                    ],
                }
                for l in self.labor
            ],
            "equipment": [
                {
                    "equipment_code": e.equipment_code,
                    "description": e.description,
                    "basis": e.basis,
                    "hours": raw(e.hours),
                    "rental_cost": fmt(e.rental_cost),
                    "fuel_litres": raw(e.fuel_litres),
                    "fuel_cost": fmt(e.fuel_cost),
                    "operating_cost": fmt(e.operating_cost),
                    "amount": fmt(e.amount),
                    "rate_source": e.rate_source,
                    "fuel_price_source": e.fuel_price_source,
                }
                for e in self.equipment
            ],
            "markups": [
                {
                    "code": k.code,
                    "label": k.label,
                    "pct": raw(k.pct),
                    "basis": k.basis,
                    "base_amount": fmt(k.base_amount),
                    "amount": fmt(k.amount),
                }
                for k in self.markups
            ],
            "totals": {k: fmt(v) for k, v in self.totals.items()},
        }


# ------------------------------------------------------------------ engines


def calculate_materials(assembly: Assembly, quantity: Decimal, rates: RateBook, as_of: date) -> list[MaterialLine]:
    lines = []
    for comp in assembly.materials:
        net = comp.consumption * quantity
        gross = net * (1 + comp.wastage_pct / HUNDRED)
        price = rates.material_price(comp.item_code, as_of, comp.supplier, comp.brand)
        try:
            priced_qty = convert(gross, comp.unit, price.unit)
        except UnitError as exc:
            raise CostingError(
                f"{assembly.code}: consumption of {comp.item_code} is in {comp.unit} but it is priced per {price.unit}"
            ) from exc
        lines.append(
            MaterialLine(
                item_code=comp.item_code,
                description=comp.description,
                category=comp.category,
                net_quantity=net,
                wastage_pct=comp.wastage_pct,
                gross_quantity=gross,
                unit=get_unit(comp.unit).symbol,
                price_unit=get_unit(price.unit).symbol,
                priced_quantity=priced_qty,
                unit_price=price.price,
                amount=priced_qty * price.price,
                supplier=price.supplier,
                brand=price.brand,
                price_source=price.source_id,
            )
        )
    return lines


def calculate_labor(
    assembly: Assembly,
    quantity: Decimal,
    rates: RateBook,
    as_of: date,
    condition: str,
    params: CostParameters,
    target_days: Decimal | None = None,
) -> list[LaborLine]:
    lines = []
    for comp in assembly.labor:
        prod = rates.productivity(comp.productivity_code, condition)
        if prod.output_per_crew_day <= 0:
            raise CostingError(f"productivity '{prod.code}' must be positive")
        try:
            # Express productivity in the assembly's unit (e.g. ft2/crew-day -> m2/crew-day).
            output = Quantity.of(prod.output_per_crew_day, prod.unit).to(assembly.unit)
        except UnitError as exc:
            raise CostingError(
                f"productivity '{prod.code}' is per {prod.unit}, assembly {assembly.code} is measured in {assembly.unit}"
            ) from exc
        crew = rates.crew(comp.crew_code)
        crew_days = quantity / output
        members = []
        for m in crew.members:
            r = rates.labor_rate(m.labor_class, as_of)
            hours = crew_days * params.hours_per_day * m.count
            members.append(LaborMemberLine(m.labor_class, m.count, hours, r.hourly_rate, hours * r.hourly_rate, r.source_id))
        if target_days:
            crews_required = (crew_days / target_days).to_integral_value(rounding="ROUND_CEILING")
        else:
            crews_required = Decimal(1)
        lines.append(
            LaborLine(
                task=comp.task,
                crew_code=crew.code,
                condition=prod.condition,
                productivity=output,
                productivity_source=prod.source_id,
                crew_days=crew_days,
                crew_size=crew.size,
                crews_required=crews_required,
                manpower=crew.size * crews_required,
                labor_hours=sum((m.hours for m in members), Decimal(0)),
                amount=sum((m.amount for m in members), Decimal(0)),
                members=tuple(members),
            )
        )
    return lines


def calculate_equipment(
    assembly: Assembly,
    quantity: Decimal,
    rates: RateBook,
    as_of: date,
    labor: list[LaborLine],
    params: CostParameters,
) -> list[EquipmentLine]:
    by_task = {l.task: l for l in labor}
    lines = []
    for comp in assembly.equipment:
        if comp.basis == "per_unit":
            hours = quantity * comp.hours_per_unit * comp.count
        else:
            hours = by_task[comp.labor_task].crew_days * params.hours_per_day * comp.count
        rate = rates.equipment_rate(comp.equipment_code, as_of)
        fuel_litres = hours * rate.fuel_litres_per_hour
        fuel_cost = Decimal(0)
        fuel_source = None
        if fuel_litres:
            if not rate.fuel_item_code:
                raise CostingError(f"equipment '{comp.equipment_code}' uses fuel but has no fuel item to price it")
            fuel = rates.material_price(rate.fuel_item_code, as_of)
            fuel_cost = convert(fuel_litres, "L", fuel.unit) * fuel.price
            fuel_source = fuel.source_id
        rental = hours * rate.rental_per_hour
        operating = hours * rate.operating_per_hour
        lines.append(
            EquipmentLine(
                equipment_code=comp.equipment_code,
                description=comp.description,
                basis=comp.basis,
                hours=hours,
                rental_cost=rental,
                fuel_litres=fuel_litres,
                fuel_cost=fuel_cost,
                operating_cost=operating,
                amount=rental + fuel_cost + operating,
                rate_source=rate.source_id,
                fuel_price_source=fuel_source,
            )
        )
    return lines


def rate_analysis(direct: Decimal, params: CostParameters) -> tuple[list[MarkupLine], Decimal]:
    lines = []
    running = direct
    for mk in params.markups:
        if mk.basis == "direct":
            base = direct
        elif mk.basis == "cumulative":
            base = running
        else:
            raise CostingError(f"unknown markup basis '{mk.basis}'")
        amount = base * mk.pct / HUNDRED
        lines.append(MarkupLine(mk.code, mk.label, mk.pct, mk.basis, base, amount))
        running += amount
    return lines, running


def calculate_item(
    assembly: Assembly,
    quantity: Decimal | int | str | Quantity,
    rates: RateBook,
    as_of: date,
    condition: str = "normal",
    target_days: Decimal | None = None,
) -> CostBreakdown:
    """Explode an assembly for a work quantity and price every resource."""
    assembly.validate()
    if isinstance(quantity, Quantity):
        qty = quantity.to(assembly.unit)
    else:
        qty = D(quantity)
    if qty < 0:
        raise CostingError("quantity cannot be negative")
    params = rates.parameters()
    with localcontext() as ctx:
        ctx.prec = ENGINE_PRECISION
        materials = calculate_materials(assembly, qty, rates, as_of)
        labor = calculate_labor(assembly, qty, rates, as_of, condition, params, target_days)
        equipment = calculate_equipment(assembly, qty, rates, as_of, labor, params)

        def cat(c: str) -> Decimal:
            return sum((m.amount for m in materials if m.category == c), Decimal(0))

        totals = {
            "material": cat("material"),
            "labor": sum((l.amount for l in labor), Decimal(0)),
            "equipment": sum((e.amount for e in equipment), Decimal(0)),
            "subcontract": cat("subcontract"),
            "other": cat("other"),
        }
        totals["direct"] = sum(totals.values(), Decimal(0))
        markups, total = rate_analysis(totals["direct"], params)
        for mk in markups:
            totals[mk.code] = totals.get(mk.code, Decimal(0)) + mk.amount
        totals["total"] = total
        totals["direct_unit_cost"] = totals["direct"] / qty if qty else Decimal(0)
        totals["unit_rate"] = total / qty if qty else Decimal(0)

    return CostBreakdown(
        assembly_ref=assembly.ref,
        quantity=qty,
        unit=get_unit(assembly.unit).symbol,
        pricing_date=as_of,
        condition=condition,
        currency=params.currency,
        materials=tuple(materials),
        labor=tuple(labor),
        equipment=tuple(equipment),
        markups=tuple(markups),
        totals=totals,
    )


def rounded(value: Decimal, places: int) -> Decimal:
    return quantize(value, places)
