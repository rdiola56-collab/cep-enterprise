"""Every estimating item type in all 16 estimating modules runs through the one shared engine.

For each item type a minimal assembly is generated in the item's own unit, so
the test proves the wiring (module → shared formula → assembly → costing) for
every module. The numbers are test inputs, not library data.
"""
from decimal import Decimal as Dec

import pytest

from app.engine.assembly import Assembly, LaborComponent, MaterialComponent
from app.engine.estimate import Estimate
from app.engine.rates import Crew, CrewMember, InMemoryRateBook, LaborRate, Productivity
from app.engine.service import EstimatingEngine, ValidationFailed
from app.modules.registry import MODULES

from . import fixtures as fx

ESTIMATING = [m for m in MODULES.values() if m.kind == "estimating"]
ITEM_TYPES = [(m, it) for m in ESTIMATING for it in m.item_types]

SAMPLE_INPUTS = {
    "CONCRETE_VOLUME": {"length": 4, "width": (400, "mm"), "height": (500, "mm"), "pieces": 2},
    "STEEL_WEIGHT": {"section_weight": (Dec("29.8"), "kg/m"), "length": 6},
    "WALL_AREA": {"length": 10, "height": Dec("2.8"), "openings": (2, "m2")},
    "FLOOR_AREA": {"length": 8, "width": 6},
    "LINEAR_LENGTH": {"length": 25, "repetitions": 4},
    "COUNT": {"count": 12, "repetitions": 3},
    "ROOM_PERIMETER": {"length": 5, "width": 4, "deductions": (900, "mm")},
    "ROOF_SLOPED_AREA": {"length": 20, "run": 6, "rise": Dec("2.5")},
    "OPENING_AREA": {"width": (1200, "mm"), "height": (1500, "mm"), "count": 8},
    "LUMP_SUM": {"lots": 1},
}
EXPECTED_QTY = {
    "CONCRETE_VOLUME": Dec("1.6"), "STEEL_WEIGHT": Dec("178.8"), "WALL_AREA": Dec(26), "FLOOR_AREA": Dec(48),
    "LINEAR_LENGTH": Dec(100), "COUNT": Dec(36), "ROOM_PERIMETER": Dec("17.1"), "ROOF_SLOPED_AREA": Dec(260),
    "OPENING_AREA": Dec("14.4"), "LUMP_SUM": Dec(1),
}


def _rig():
    lib = fx.formula_library()
    assemblies, prices, prods = {}, [fx.price("PS-LIFTS", "lot", "12500000.00")], []
    for _, it in ITEM_TYPES:
        if it.priced_directly:
            continue
        unit = lib.get(it.formula_code).output_unit
        code = it.default_assembly
        assemblies[code] = Assembly(code, code, "T", unit,
                                    materials=(MaterialComponent(f"MAT-{code}", "m", Dec(1), unit, Dec(5)),),
                                    labor=(LaborComponent("work", "GANG", f"PROD-{code}"),))
        prices.append(fx.price(f"MAT-{code}", unit, "100"))
        prods.append(Productivity(f"PROD-{code}", "normal", Dec(10), unit))
    rates = InMemoryRateBook(fx.NO_MARKUP, prices=prices, labor_rates=[LaborRate("worker", Dec(50), "PHP", fx.D0)],
                             crews=[Crew("GANG", "gang", (CrewMember("worker", Dec(2)),))], productivities=prods)
    return EstimatingEngine(lib.get, assemblies.__getitem__, rates)


@pytest.mark.parametrize("module,item_type", ITEM_TYPES, ids=[f"{m.number:02d}-{it.code}" for m, it in ITEM_TYPES])
def test_every_item_type_calculates_through_the_shared_engine(module, item_type):
    eng = _rig()
    extra = {"priced_item_code": "PS-LIFTS"} if item_type.priced_directly else {}
    item = eng.calculate_item(item_id="i", module=module.code, item_type=item_type.code,
                              inputs=SAMPLE_INPUTS[item_type.formula_code], as_of=fx.AS_OF, **extra)
    assert item.quantity == EXPECTED_QTY[item_type.formula_code]
    assert item.amount > 0
    assert item.quantity_source.formula_code == item_type.formula_code
    if item_type.priced_directly:
        assert item.amount == Dec("12500000.00") and item.breakdown.totals["other"] == item.amount
    else:
        # 1 unit material at 100 with 5 % wastage; labor 2 workers × 8 h × 50 per 10 units
        assert item.amount == item.quantity * (Dec(105) + Dec(80))


def test_sixteen_estimating_modules_with_thirty_nine_item_types():
    assert len(ESTIMATING) == 16
    assert len(ITEM_TYPES) == 39


def test_shared_formulas_are_reused_across_modules():
    users: dict[str, set[str]] = {}
    for m, it in ITEM_TYPES:
        users.setdefault(it.formula_code, set()).add(m.code)
    assert len(users["COUNT"]) >= 5
    assert len(users["LINEAR_LENGTH"]) >= 6
    assert len(users["FLOOR_AREA"]) >= 5
    assert len(users) == 10  # 39 item types served by 10 shared formulas


def test_provisional_sum_needs_a_price_book_item_and_rejects_assemblies():
    eng = _rig()
    with pytest.raises(ValidationFailed, match="price-book item"):
        eng.calculate_item(item_id="p", module="provisional_sums", item_type="allowance", inputs={}, as_of=fx.AS_OF)
    with pytest.raises(ValidationFailed, match="not directly"):
        eng.calculate_item(item_id="d", module="tiling", item_type="floor_tile", inputs=SAMPLE_INPUTS["FLOOR_AREA"],
                           as_of=fx.AS_OF, priced_item_code="PS-LIFTS")


def test_one_master_estimate_from_all_modules():
    eng = _rig()
    est = Estimate("tower")
    for n, (m, it) in enumerate(ITEM_TYPES):
        extra = {"priced_item_code": "PS-LIFTS"} if it.priced_directly else {}
        est.upsert(eng.calculate_item(item_id=f"{m.code}:{it.code}", module=m.code, item_type=it.code,
                                      inputs=SAMPLE_INPUTS[it.formula_code], as_of=fx.AS_OF,
                                      location={"building": "T1", "floor": f"L{n % 3 + 1:02d}"}, **extra))
    assert len(est.boq()) == 39
    by_module = est.consolidate(("module",))
    assert len(by_module) == 16
    assert sum((r["amount"] for r in by_module), Dec(0)) == est.total
    assert sum((r["amount"] for r in est.consolidate(("building", "floor", "trade"))), Dec(0)) == est.total
