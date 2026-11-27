"""Estimate items, master-estimate consolidation (BOQ and summaries) and revision comparison.

Items are keyed by a stable ``item_id``. Recalculating an item replaces it, so
consolidation can never double-count an updated item.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping

from .costing import CostBreakdown
from .formula.evaluator import FormulaResult

LOCATION_LEVELS = ("building", "floor", "zone", "room")


@dataclass(frozen=True)
class EstimateItem:
    item_id: str
    module: str  # estimating module code, e.g. "reinforced_concrete"
    trade: str
    description: str
    assembly_code: str
    breakdown: CostBreakdown
    location: Mapping[str, str] = field(default_factory=dict)  # building/floor/zone/room
    quantity_source: FormulaResult | None = None
    priced_item_code: str | None = None

    @property
    def quantity(self) -> Decimal:
        return self.breakdown.quantity

    @property
    def unit(self) -> str:
        return self.breakdown.unit

    @property
    def amount(self) -> Decimal:
        return self.breakdown.total_amount


class Estimate:
    def __init__(self, project_id: str, items: Iterable[EstimateItem] = ()):
        self.project_id = project_id
        self._items: dict[str, EstimateItem] = {}
        for item in items:
            self.upsert(item)

    def upsert(self, item: EstimateItem) -> None:
        self._items[item.item_id] = item

    def remove(self, item_id: str) -> None:
        self._items.pop(item_id, None)

    @property
    def items(self) -> list[EstimateItem]:
        return list(self._items.values())

    @property
    def total(self) -> Decimal:
        return sum((i.amount for i in self._items.values()), Decimal(0))

    # ---------------------------------------------------------- consolidation

    def consolidate(self, by: tuple[str, ...]) -> list[dict]:
        """Sum amounts by any of: module, trade, assembly, building, floor, zone, room."""
        groups: dict[tuple, dict] = {}
        for item in self._items.values():
            key = tuple(self._dim(item, d) for d in by)
            g = groups.setdefault(key, {**dict(zip(by, key)), "amount": Decimal(0), "items": 0})
            g["amount"] += item.amount
            g["items"] += 1
        return sorted(groups.values(), key=lambda g: tuple(str(g[d]) for d in by))

    @staticmethod
    def _dim(item: EstimateItem, d: str) -> str:
        if d == "module":
            return item.module
        if d == "trade":
            return item.trade
        if d == "assembly":
            return item.assembly_code
        if d in LOCATION_LEVELS:
            return item.location.get(d, "")
        raise ValueError(f"unknown consolidation dimension '{d}'")

    def boq(self) -> list[dict]:
        """Bill of quantities: one line per work item type (assembly + unit), quantities and amounts summed."""
        lines: dict[tuple, dict] = {}
        for item in self._items.values():
            key = (item.trade, item.assembly_code, item.unit)
            line = lines.setdefault(
                key,
                {
                    "trade": item.trade,
                    "assembly_code": item.assembly_code,
                    "description": item.description,
                    "unit": item.unit,
                    "quantity": Decimal(0),
                    "amount": Decimal(0),
                    "source_items": [],
                },
            )
            line["quantity"] += item.quantity
            line["amount"] += item.amount
            line["source_items"].append(item.item_id)
        out = []
        for line in sorted(lines.values(), key=lambda l: (l["trade"], l["assembly_code"])):
            line["unit_rate"] = line["amount"] / line["quantity"] if line["quantity"] else Decimal(0)
            out.append(line)
        return out

    def material_summary(self) -> list[dict]:
        rows: dict[tuple, dict] = defaultdict(lambda: {"quantity": Decimal(0), "amount": Decimal(0)})
        for item in self._items.values():
            for m in item.breakdown.materials:
                r = rows[(m.item_code, m.price_unit, m.category)]
                r.update(item_code=m.item_code, description=m.description, unit=m.price_unit, category=m.category)
                r["quantity"] += m.priced_quantity
                r["amount"] += m.amount
        return sorted(rows.values(), key=lambda r: r["item_code"])

    def labor_summary(self) -> list[dict]:
        rows: dict[str, dict] = defaultdict(lambda: {"hours": Decimal(0), "amount": Decimal(0)})
        for item in self._items.values():
            for l in item.breakdown.labor:
                for m in l.members:
                    r = rows[m.labor_class]
                    r["labor_class"] = m.labor_class
                    r["hours"] += m.hours
                    r["amount"] += m.amount
        return sorted(rows.values(), key=lambda r: r["labor_class"])

    def equipment_summary(self) -> list[dict]:
        rows: dict[str, dict] = defaultdict(lambda: {"hours": Decimal(0), "fuel_litres": Decimal(0), "amount": Decimal(0)})
        for item in self._items.values():
            for e in item.breakdown.equipment:
                r = rows[e.equipment_code]
                r["equipment_code"] = e.equipment_code
                r["hours"] += e.hours
                r["fuel_litres"] += e.fuel_litres
                r["amount"] += e.amount
        return sorted(rows.values(), key=lambda r: r["equipment_code"])

    def cost_summary(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for item in self._items.values():
            for k, v in item.breakdown.totals.items():
                if k not in ("unit_rate", "direct_unit_cost"):
                    out[k] += v
        return dict(out)

    def snapshot(self) -> dict[str, dict]:
        """Comparable, serialisable view of every item (full precision)."""
        return {
            i.item_id: {
                "description": i.description,
                "module": i.module,
                "trade": i.trade,
                "assembly_code": i.assembly_code,
                "quantity": str(i.quantity),
                "unit": i.unit,
                "amount": str(i.amount),
                "unit_rate": str(i.breakdown.unit_rate),
            }
            for i in self._items.values()
        }


def compare_snapshots(old: Mapping[str, Mapping], new: Mapping[str, Mapping]) -> dict:
    """Revision comparison between two estimate snapshots (``Estimate.snapshot()`` or DB equivalent)."""
    changes = []
    for item_id in sorted(old.keys() | new.keys()):
        a, b = old.get(item_id), new.get(item_id)
        if a is None:
            status = "added"
        elif b is None:
            status = "removed"
        elif (a["quantity"], a["amount"], a["unit"]) != (b["quantity"], b["amount"], b["unit"]):
            status = "changed"
        else:
            continue
        qa = Decimal(a["quantity"]) if a else Decimal(0)
        qb = Decimal(b["quantity"]) if b else Decimal(0)
        aa = Decimal(a["amount"]) if a else Decimal(0)
        ab = Decimal(b["amount"]) if b else Decimal(0)
        changes.append(
            {
                "item_id": item_id,
                "status": status,
                "description": (b or a)["description"],
                "unit": (b or a)["unit"],
                "old_quantity": qa,
                "new_quantity": qb,
                "quantity_delta": qb - qa,
                "old_amount": aa,
                "new_amount": ab,
                "amount_delta": ab - aa,
            }
        )
    old_total = sum((Decimal(v["amount"]) for v in old.values()), Decimal(0))
    new_total = sum((Decimal(v["amount"]) for v in new.values()), Decimal(0))
    return {"old_total": old_total, "new_total": new_total, "delta": new_total - old_total, "changes": changes}
