"""Mandatory acceptance tests, engine level (spec §12).

Database-backed versions of tests 2, 6 and 7 (project isolation, immutable
approved snapshots) live in tests/test_acceptance_db.py.
"""
from dataclasses import replace
from decimal import Decimal as Dec

import pytest

from app.engine.costing import calculate_item
from app.engine.estimate import Estimate, compare_snapshots
from app.engine.precision import money
from app.engine.service import EstimatingEngine, ValidationFailed
from app.modules.registry import MODULES

from .fixtures import ASSEMBLIES, AS_OF, BASE_PRICES, NO_MARKUP, RC_BEAM, TILE_FLOOR, formula_library, price, ratebook


def engine(rates=None, assemblies=None):
    lib = formula_library()
    assemblies = assemblies or ASSEMBLIES
    return EstimatingEngine(formulas=lib.get, assemblies=assemblies.__getitem__, rates=rates or ratebook())


# AT-1 ---------------------------------------------------------------------------


def test_at1_one_formula_shared_across_modules():
    """FLOOR_AREA is defined once and measures items in Tiling Works and Ceiling Works."""
    eng = engine()
    tiling = MODULES["tiling"].item_type("floor_tile")
    ceiling = MODULES["ceiling"].item_type("ceiling_board")
    assert tiling.formula_code == ceiling.formula_code == "FLOOR_AREA"

    inputs = {"length": (Dec("8.5"), "m"), "width": (6, "m"), "repetitions": 12}
    a = eng.calculate_item(item_id="t1", module="tiling", item_type="floor_tile", inputs=inputs, as_of=AS_OF)
    b = eng.calculate_item(item_id="c1", module="ceiling", item_type="ceiling_board", inputs=inputs, as_of=AS_OF)

    assert a.quantity == b.quantity == Dec("612")
    assert a.quantity_source.formula_hash == b.quantity_source.formula_hash
    assert a.quantity_source.formula_version == b.quantity_source.formula_version == 1
    # Different assemblies price the same quantity differently.
    assert a.breakdown.assembly_ref.startswith("TILE-FLR600") and b.breakdown.assembly_ref.startswith("CLG-BOARD")
    assert a.amount != b.amount


def test_all_19_modules_are_configured_and_workspaces_take_no_items():
    assert len(MODULES) == 19
    assert all(m.status == "configured" for m in MODULES.values())
    assert {m.number for m in MODULES.values() if m.kind != "estimating"} == {17, 18, 19}
    with pytest.raises(ValidationFailed, match="does not take estimate items"):
        engine().calculate_item(item_id="x", module="materials_pricelist", item_type="any", inputs={}, as_of=AS_OF)


def test_module_validation_rules():
    with pytest.raises(ValidationFailed, match="beam depth"):
        engine().calculate_item(
            item_id="b", module="reinforced_concrete", item_type="beam",
            inputs={"length": 6, "width": (300, "mm"), "height": (3500, "mm")}, as_of=AS_OF,
        )


# AT-2 ---------------------------------------------------------------------------


def test_at2_one_assembly_two_projects_different_prices():
    tower_a = ratebook(NO_MARKUP)
    tower_b = ratebook(NO_MARKUP, prices=[p for p in BASE_PRICES if p.item_code != "RMC-4000"] + [price("RMC-4000", "m3", "6100.00")])
    a = calculate_item(RC_BEAM, 10, tower_a, AS_OF)
    b = calculate_item(RC_BEAM, 10, tower_b, AS_OF)
    conc = lambda bd: next(m for m in bd.materials if m.item_code == "RMC-4000")  # noqa: E731
    assert conc(a).amount == Dec("10.5") * 5200
    assert conc(b).amount == Dec("10.5") * 6100
    assert b.total_amount - a.total_amount == Dec("10.5") * 900
    assert a.assembly_ref == b.assembly_ref == "RC-BEAM@1[master]"


def test_at2_project_customised_assembly_leaves_master_untouched():
    custom = TILE_FLOOR.customize_for_project("tower-b", materials=(replace(TILE_FLOOR.materials[0], wastage_pct=Dec(8)), TILE_FLOOR.materials[1]))
    assert TILE_FLOOR.materials[0].wastage_pct == 5
    assert custom.ref == "TILE-FLR600@1[project:tower-b]" and custom.parent_revision == "TILE-FLR600@1"
    assert calculate_item(custom, 100, ratebook(), AS_OF).total_amount > calculate_item(TILE_FLOOR, 100, ratebook(), AS_OF).total_amount


# AT-5 ---------------------------------------------------------------------------


def _estimate(eng):
    est = Estimate("tower-a")
    loc = lambda f: {"building": "Tower A", "floor": f}  # noqa: E731
    for floor in ("L01", "L02", "L03"):
        est.upsert(eng.calculate_item(item_id=f"tile-{floor}", module="tiling", item_type="floor_tile",
                                      inputs={"length": 20, "width": 15}, as_of=AS_OF, location=loc(floor)))
        est.upsert(eng.calculate_item(item_id=f"beam-{floor}", module="reinforced_concrete", item_type="beam",
                                      inputs={"length": 6, "width": (300, "mm"), "height": (600, "mm"), "pieces": 10},
                                      as_of=AS_OF, location=loc(floor)))
    return est


def test_at5_boq_consolidation_without_duplicates():
    eng = engine()
    est = _estimate(eng)
    boq = {l["assembly_code"]: l for l in est.boq()}
    assert boq["TILE-FLR600"]["quantity"] == 900  # 3 floors × 300 m²
    assert money(boq["RC-BEAM"]["quantity"], 3) == Dec("32.400")  # 3 × 10 × 6 × 0.3 × 0.6
    assert sorted(boq["TILE-FLR600"]["source_items"]) == ["tile-L01", "tile-L02", "tile-L03"]
    total_before = est.total

    # Recalculating an item (revised dimensions) replaces it; nothing is double-counted.
    est.upsert(eng.calculate_item(item_id="tile-L02", module="tiling", item_type="floor_tile",
                                  inputs={"length": 20, "width": 10}, as_of=AS_OF,
                                  location={"building": "Tower A", "floor": "L02"}))
    boq = {l["assembly_code"]: l for l in est.boq()}
    assert boq["TILE-FLR600"]["quantity"] == 800
    assert len(est.items) == 6
    assert est.total < total_before

    by_floor = {r["floor"]: r for r in est.consolidate(("building", "floor"))}
    assert set(by_floor) == {"L01", "L02", "L03"}
    assert sum((r["amount"] for r in by_floor.values()), Dec(0)) == est.total
    by_trade = est.consolidate(("trade",))
    assert sum((r["amount"] for r in by_trade), Dec(0)) == est.total

    mats = {m["item_code"]: m for m in est.material_summary()}
    assert money(mats["TILE-600"]["quantity"], 3) == money(Dec(800) * Dec("2.78") * Dec("1.05"), 3)


# AT-7 ---------------------------------------------------------------------------


def test_at7_revision_comparison():
    eng = engine()
    rev1 = _estimate(eng).snapshot()
    est2 = _estimate(eng)
    est2.upsert(eng.calculate_item(item_id="tile-L03", module="tiling", item_type="floor_tile",
                                   inputs={"length": 20, "width": 18}, as_of=AS_OF))
    est2.remove("beam-L01")
    est2.upsert(eng.calculate_item(item_id="wall-L01", module="walls_partitions", item_type="chb_wall",
                                   inputs={"length": 30, "height": 3, "openings": (Dec("4.2"), "m2")}, as_of=AS_OF))
    diff = compare_snapshots(rev1, est2.snapshot())
    status = {c["item_id"]: c["status"] for c in diff["changes"]}
    assert status == {"tile-L03": "changed", "beam-L01": "removed", "wall-L01": "added"}
    tile = next(c for c in diff["changes"] if c["item_id"] == "tile-L03")
    assert tile["quantity_delta"] == 60
    assert diff["delta"] == sum((c["amount_delta"] for c in diff["changes"]), Dec(0))


# AT-10 --------------------------------------------------------------------------


def test_at10_cost_report_reconciles():
    est = _estimate(engine())
    summary = est.cost_summary()
    assert abs(summary["total"] - est.total) < Dec("1e-20")
    parts = summary["direct"] + sum((summary[k] for k in ("overhead", "contingency", "profit", "tax")), Dec(0))
    assert abs(parts - summary["total"]) < Dec("1e-20")
    direct_parts = sum((summary[k] for k in ("material", "labor", "equipment", "subcontract", "other")), Dec(0))
    assert abs(direct_parts - summary["direct"]) < Dec("1e-20")
    assert abs(sum((r["amount"] for r in est.material_summary()), Dec(0)) - summary["material"]) < Dec("1e-20")
    assert abs(sum((r["amount"] for r in est.labor_summary()), Dec(0)) - summary["labor"]) < Dec("1e-20")
    assert abs(sum((r["amount"] for r in est.equipment_summary()), Dec(0)) - summary["equipment"]) < Dec("1e-20")
    # Per-item hand check for one tiling line (300 m²): 3 × the 100 m² reference case.
    tile = next(i for i in est.items if i.item_id == "tile-L01")
    assert money(tile.amount) == money(Dec("62584.43333333333333") * 3)
