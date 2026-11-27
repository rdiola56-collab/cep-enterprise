"""Rate records and the RateBook interface.

The engine never hard-codes a price, wage, productivity or equipment rate.
Every rate comes from a :class:`RateBook`, which resolves records for one
project at one pricing date. The production RateBook reads PostgreSQL
(``app.services.ratebook.SqlRateBook``); :class:`InMemoryRateBook` holds the
same records in memory for engine-level use and tests.

Every resolved record carries ``source_id`` so the cost breakdown can be
traced back to the exact price/rate revision that was used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, Sequence

PRODUCTIVITY_CONDITIONS = ("normal", "high_rise", "commercial", "basement", "difficult_access")


class RateNotFound(LookupError):
    pass


@dataclass(frozen=True)
class MaterialPrice:
    """A price for a priced resource: material, fuel, subcontract or other direct cost."""

    item_code: str
    unit: str
    price: Decimal
    currency: str
    valid_from: date
    valid_to: date | None = None
    supplier: str | None = None
    brand: str | None = None
    category: str = "material"  # material | fuel | subcontract | other
    revision: int = 1
    source_id: str = ""


@dataclass(frozen=True)
class LaborRate:
    labor_class: str
    hourly_rate: Decimal
    currency: str
    valid_from: date
    valid_to: date | None = None
    source_id: str = ""


@dataclass(frozen=True)
class CrewMember:
    labor_class: str
    count: Decimal


@dataclass(frozen=True)
class Crew:
    code: str
    name: str
    members: tuple[CrewMember, ...]
    source_id: str = ""

    @property
    def size(self) -> Decimal:
        return sum((m.count for m in self.members), Decimal(0))


@dataclass(frozen=True)
class Productivity:
    """Output of one crew in one crew-day, under one working condition."""

    code: str
    condition: str
    output_per_crew_day: Decimal
    unit: str
    source_id: str = ""


@dataclass(frozen=True)
class EquipmentRate:
    equipment_code: str
    rental_per_hour: Decimal
    currency: str
    valid_from: date
    valid_to: date | None = None
    fuel_litres_per_hour: Decimal = Decimal(0)
    fuel_item_code: str | None = None
    operating_per_hour: Decimal = Decimal(0)  # operator-less running costs: lubricants, wear parts
    source_id: str = ""


@dataclass(frozen=True)
class Markup:
    code: str  # overhead | contingency | profit | tax | any configured code
    label: str
    pct: Decimal
    basis: str = "direct"  # "direct" = on direct cost; "cumulative" = on direct + all earlier markups


@dataclass(frozen=True)
class CostParameters:
    currency: str
    hours_per_day: Decimal
    markups: tuple[Markup, ...]
    productivity_factors: dict[str, Decimal] = field(default_factory=dict)  # condition -> factor on normal output
    money_places: int = 2
    quantity_places: int = 3
    source_id: str = ""


class RateBook(Protocol):
    def material_price(
        self, item_code: str, as_of: date, supplier: str | None = None, brand: str | None = None
    ) -> MaterialPrice: ...

    def labor_rate(self, labor_class: str, as_of: date) -> LaborRate: ...

    def crew(self, code: str) -> Crew: ...

    def productivity(self, code: str, condition: str) -> Productivity: ...

    def equipment_rate(self, equipment_code: str, as_of: date) -> EquipmentRate: ...

    def parameters(self) -> CostParameters: ...


def _valid(valid_from: date, valid_to: date | None, as_of: date) -> bool:
    return valid_from <= as_of and (valid_to is None or as_of <= valid_to)


def pick_price(candidates: Sequence[MaterialPrice], item_code: str, as_of: date, supplier: str | None, brand: str | None) -> MaterialPrice:
    rows = [
        p
        for p in candidates
        if p.item_code == item_code
        and _valid(p.valid_from, p.valid_to, as_of)
        and (supplier is None or p.supplier == supplier)
        and (brand is None or p.brand == brand)
    ]
    if not rows:
        qual = "".join(f" {k}={v}" for k, v in (("supplier", supplier), ("brand", brand)) if v)
        raise RateNotFound(f"no price for '{item_code}' valid on {as_of.isoformat()}{qual}")
    return max(rows, key=lambda p: (p.valid_from, p.revision))


def resolve_productivity(records: Sequence[Productivity], factors: dict[str, Decimal], code: str, condition: str) -> Productivity:
    if condition not in PRODUCTIVITY_CONDITIONS:
        raise RateNotFound(f"unknown productivity condition '{condition}'")
    exact = [p for p in records if p.code == code and p.condition == condition]
    if exact:
        return exact[-1]
    base = [p for p in records if p.code == code and p.condition == "normal"]
    if not base:
        raise RateNotFound(f"no productivity '{code}' for condition '{condition}' or 'normal'")
    if condition == "normal":
        return base[-1]
    factor = factors.get(condition)
    if factor is None:
        raise RateNotFound(
            f"no productivity '{code}' for '{condition}' and no '{condition}' factor configured in cost parameters"
        )
    b = base[-1]
    return Productivity(code, condition, b.output_per_crew_day * factor, b.unit, f"{b.source_id}×factor:{condition}")


class InMemoryRateBook:
    """RateBook over explicit record lists, resolving by validity date and revision like the SQL implementation."""

    def __init__(
        self,
        parameters: CostParameters,
        prices: Sequence[MaterialPrice] = (),
        labor_rates: Sequence[LaborRate] = (),
        crews: Sequence[Crew] = (),
        productivities: Sequence[Productivity] = (),
        equipment_rates: Sequence[EquipmentRate] = (),
    ) -> None:
        self._params = parameters
        self._prices = list(prices)
        self._labor = list(labor_rates)
        self._crews = {c.code: c for c in crews}
        self._prod = list(productivities)
        self._equip = list(equipment_rates)

    def material_price(self, item_code, as_of, supplier=None, brand=None):
        return pick_price(self._prices, item_code, as_of, supplier, brand)

    def labor_rate(self, labor_class, as_of):
        rows = [r for r in self._labor if r.labor_class == labor_class and _valid(r.valid_from, r.valid_to, as_of)]
        if not rows:
            raise RateNotFound(f"no labor rate for '{labor_class}' valid on {as_of.isoformat()}")
        return max(rows, key=lambda r: r.valid_from)

    def crew(self, code):
        try:
            return self._crews[code]
        except KeyError:
            raise RateNotFound(f"no crew '{code}'") from None

    def productivity(self, code, condition):
        return resolve_productivity(self._prod, self._params.productivity_factors, code, condition)

    def equipment_rate(self, equipment_code, as_of):
        rows = [r for r in self._equip if r.equipment_code == equipment_code and _valid(r.valid_from, r.valid_to, as_of)]
        if not rows:
            raise RateNotFound(f"no equipment rate for '{equipment_code}' valid on {as_of.isoformat()}")
        return max(rows, key=lambda r: r.valid_from)

    def parameters(self):
        return self._params
