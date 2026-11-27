"""Mandatory acceptance tests against PostgreSQL (spec §12, tests 2, 5, 6, 7, 10)."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as Dec

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import models as m
from app.engine.formula import FormulaDefinition, InputSpec
from app.engine.precision import money
from app.services import estimates as est
from app.services import library as lib

from . import fixtures as fx
from .db_seed import seed_master

AS_OF = fx.AS_OF
TILE_INPUTS = {"length": {"value": "20", "unit": "m"}, "width": {"value": "5", "unit": "m"}}  # 100 m²
BEAM_INPUTS = {"length": {"value": "6", "unit": "m"}, "width": {"value": "300", "unit": "mm"},
               "height": {"value": "600", "unit": "mm"}, "pieces": {"value": "10", "unit": "pcs"}}


@pytest.fixture
def two_projects(db):
    seed_master(db)
    a = est.create_project(db, "TWR-A", "Tower A", "PHP")
    b = est.create_project(db, "TWR-B", "Tower B", "PHP")
    return a, b


def frozen(db):
    """Expect the database itself to refuse the statement."""
    return pytest.raises(DBAPIError, match="CEP:")


# AT-2 / AT-6 -------------------------------------------------------------------


def test_at2_at6_same_assembly_project_specific_prices_are_isolated(db, two_projects):
    a, b = two_projects
    # Tower B negotiates its own tile price; Tower A uses the master price.
    lib.add_material_price(db, "TILE-600", "pcs", "70.00", "PHP", date(2026, 1, 1), project_id=b.id)
    va = est.create_estimate(db, a.id, "EST", "Tower A estimate", AS_OF)
    vb = est.create_estimate(db, b.id, "EST", "Tower B estimate", AS_OF)
    ia = est.calculate_item(db, va.id, item_key="tile-L1", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    ib = est.calculate_item(db, vb.id, item_key="tile-L1", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)

    assert ia.assembly_revision_id == ib.assembly_revision_id  # one assembly, two projects
    tiles = lambda item: next(x for x in item.breakdown["materials"] if x["item_code"] == "TILE-600")  # noqa: E731
    assert Dec(tiles(ia)["unit_price"]) == 85 and Dec(tiles(ib)["unit_price"]) == 70
    assert money(ia.amount) == Dec("62584.43")  # the hand-checked reference figure
    # 291.9 tiles × 15 cheaper, then the 1.25 × 1.12 markup chain
    assert money(ia.amount - ib.amount) == money(Dec("291.9") * 15 * Dec("1.25") * Dec("1.12"))
    assert tiles(ib)["price_source"].startswith("material_prices:")


def test_at6_one_projects_price_change_never_reaches_another_projects_estimate(db, two_projects):
    a, b = two_projects
    va = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    est.calculate_item(db, va.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    est.approve(db, va.id, "estimator.a")
    before = est.snapshot(db, va.id)

    # Tower B adds its own prices and assembly customisation.
    lib.add_material_price(db, "TILE-600", "pcs", "999", "PHP", date(2026, 1, 1), project_id=b.id)
    lib.customize_assembly_for_project(db, "TILE-FLR600", b.id, materials=(replace(fx.TILE_FLOOR.materials[0], wastage_pct=Dec(12)),))
    # And the master price changes too (new revision, later validity).
    lib.add_material_price(db, "TILE-600", "pcs", "95", "PHP", date(2026, 10, 1))

    assert est.snapshot(db, va.id) == before  # approved estimate untouched
    # A *new* Tower A revision sees the master change but never Tower B's records.
    v2 = est.new_revision(db, va.id)
    (item,) = est.items(db, v2.id)
    tile = next(x for x in item.breakdown["materials"] if x["item_code"] == "TILE-600")
    assert Dec(tile["unit_price"]) == 95
    assert Dec(tile["wastage_pct"]) == 5


def test_at6_rows_cannot_cross_projects(db, two_projects):
    a, b = two_projects
    va = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    item = est.calculate_item(db, va.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(update(m.EstimateItemRecord).where(m.EstimateItemRecord.id == item.id).values(project_id=b.id))
    with pytest.raises(LookupError):
        est.calculate_item(db, va.id, item_key="x", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS, project_id=b.id)


# Approved snapshots are immutable ------------------------------------------------


def test_approved_estimate_is_frozen_in_service_and_database(db, two_projects):
    a, _ = two_projects
    v = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    item = est.calculate_item(db, v.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    est.approve(db, v.id, "pm")
    with pytest.raises(est.EstimateLocked):
        est.calculate_item(db, v.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    with frozen(db), db.begin_nested():
        db.execute(update(m.EstimateItemRecord).where(m.EstimateItemRecord.id == item.id).values(amount=1))
    with frozen(db), db.begin_nested():
        db.execute(update(m.CostComponent).where(m.CostComponent.estimate_item_id == item.id).values(amount=1))
    with frozen(db), db.begin_nested():
        db.execute(update(m.EstimateVersion).where(m.EstimateVersion.id == v.id).values(pricing_date=date(2027, 1, 1)))
    with frozen(db), db.begin_nested():
        db.execute(text("DELETE FROM boq_items WHERE estimate_version_id = :v"), {"v": v.id})


def test_rates_are_append_only(db, two_projects):
    row = db.scalars(select(m.MaterialPrice).where(m.MaterialPrice.material_code == "TILE-600")).first()
    with frozen(db), db.begin_nested():
        db.execute(update(m.MaterialPrice).where(m.MaterialPrice.id == row.id).values(price=1))
    with frozen(db), db.begin_nested():
        db.execute(text("DELETE FROM material_prices WHERE id = :i"), {"i": row.id})
    # Closing a validity window is the one permitted change.
    db.execute(update(m.MaterialPrice).where(m.MaterialPrice.id == row.id).values(valid_to=date(2026, 12, 31)))
    fv = db.scalars(select(m.FormulaVersion)).first()
    with frozen(db), db.begin_nested():
        db.execute(update(m.FormulaVersion).where(m.FormulaVersion.id == fv.id).values(expression="1"))


# AT-1 through the database: formula versions are traceable -------------------------


def test_formula_versioning_is_traced_per_item(db, two_projects):
    a, _ = two_projects
    v = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    i1 = est.calculate_item(db, v.id, item_key="t1", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    v1_id = i1.formula_version_id
    lib.publish_formula(db, FormulaDefinition(
        "FLOOR_AREA", "Floor area", "length × width × repetitions × factor", "area", "m2",
        (InputSpec("length", "length", "m"), InputSpec("width", "length", "m"),
         InputSpec("repetitions", "dimensionless", "pcs", default=Dec(1)),
         InputSpec("factor", "dimensionless", "1", default=Dec(1))),
    ))
    i2 = est.calculate_item(db, v.id, item_key="t2", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    c1 = est.calculate_item(db, v.id, item_key="c1", module="ceiling", item_type="ceiling_board", inputs=TILE_INPUTS)
    assert i2.formula_version_id != v1_id and i2.formula_version_id == c1.formula_version_id
    take = db.scalars(select(m.QuantityTakeoff).where(m.QuantityTakeoff.estimate_item_id == i1.id)).one()
    assert take.formula_version_id == v1_id and take.methodology.startswith("([20 m] × [5 m])")
    dims = {d.name: d.value for d in take.dimensions}
    assert dims == {"length": 20, "width": 5, "repetitions": 1}


# AT-5 -----------------------------------------------------------------------------


def test_at5_boq_consolidation_in_database(db, two_projects):
    a, _ = two_projects
    v = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    for f in ("L01", "L02", "L03"):
        est.calculate_item(db, v.id, item_key=f"tile-{f}", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
        est.calculate_item(db, v.id, item_key=f"beam-{f}", module="reinforced_concrete", item_type="beam", inputs=BEAM_INPUTS)
    # recalculating does not duplicate
    est.calculate_item(db, v.id, item_key="tile-L01", module="tiling", item_type="floor_tile",
                       inputs={**TILE_INPUTS, "width": {"value": "10", "unit": "m"}})
    lines = {l["assembly_code"]: l for l in est.boq(db, v.id)}
    assert lines["TILE-FLR600"]["quantity"] == 400
    assert lines["TILE-FLR600"]["source_item_keys"] == ["tile-L01", "tile-L02", "tile-L03"]
    assert money(lines["RC-BEAM"]["quantity"], 3) == Dec("32.400")
    assert len(est.items(db, v.id)) == 6
    est.approve(db, v.id, "pm")
    stored = db.scalars(select(m.BoqItem).where(m.BoqItem.estimate_version_id == v.id).order_by(m.BoqItem.line_no)).all()
    assert [b.assembly_code for b in stored] == ["TILE-FLR600", "RC-BEAM"]
    assert money(sum((b.amount for b in stored), Dec(0))) == money(db.get(m.EstimateVersion, v.id).total_amount)


# AT-7 -----------------------------------------------------------------------------


def test_at7_revision_comparison_in_database(db, two_projects):
    a, _ = two_projects
    v1 = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    est.calculate_item(db, v1.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    est.calculate_item(db, v1.id, item_key="beam", module="reinforced_concrete", item_type="beam", inputs=BEAM_INPUTS)
    est.approve(db, v1.id, "pm")

    lib.add_material_price(db, "RMC-4000", "m3", "5600.00", "PHP", date(2026, 10, 1))  # concrete price rise
    v2 = est.new_revision(db, v1.id)
    est.calculate_item(db, v2.id, item_key="tile", module="tiling", item_type="floor_tile",
                       inputs={**TILE_INPUTS, "width": {"value": "6", "unit": "m"}})
    est.calculate_item(db, v2.id, item_key="wall", module="walls_partitions", item_type="chb_wall",
                       inputs={"length": {"value": "30", "unit": "m"}, "height": {"value": "3", "unit": "m"}})
    diff = est.compare_versions(db, v1.id, v2.id)
    by = {c["item_id"]: c for c in diff["changes"]}
    assert by["tile"]["status"] == "changed" and by["tile"]["quantity_delta"] == 20
    assert by["beam"]["status"] == "changed" and by["beam"]["quantity_delta"] == 0  # price-only change
    # 10.8 m³ × 1.05 wastage × 400 increase, through the markup chain
    assert money(by["beam"]["amount_delta"]) == money(Dec("10.8") * Dec("1.05") * 400 * Dec("1.25") * Dec("1.12"))
    assert by["wall"]["status"] == "added"
    assert db.get(m.EstimateVersion, v1.id).status == "approved"
    est.approve(db, v2.id, "pm")
    assert db.get(m.EstimateVersion, v1.id).status == "superseded"


# AT-10 ----------------------------------------------------------------------------


def test_at10_database_cost_reports_reconcile(db, two_projects):
    a, _ = two_projects
    v = est.create_estimate(db, a.id, "EST", "A", AS_OF)
    est.calculate_item(db, v.id, item_key="tile", module="tiling", item_type="floor_tile", inputs=TILE_INPUTS)
    est.calculate_item(db, v.id, item_key="beam", module="reinforced_concrete", item_type="beam", inputs=BEAM_INPUTS)
    summary = est.cost_summary(db, v.id)
    total_items = sum((i.amount for i in est.items(db, v.id)), Dec(0))
    assert money(summary["total"]) == money(total_items)
    mats = sum((r["amount"] for r in est.resource_summary(db, v.id, "material")), Dec(0))
    labor = sum((r["amount"] for r in est.resource_summary(db, v.id, "labor")), Dec(0))
    equip = sum((r["amount"] for r in est.resource_summary(db, v.id, "equipment")), Dec(0))
    assert money(mats) == money(summary["material"])
    assert money(labor) == money(summary["labor"])
    assert money(equip) == money(summary["equipment"])
    assert money(mats + labor + equip) == money(summary["direct"])
    # Tile line alone is the hand-checked 62,584.43.
    tile = next(i for i in est.items(db, v.id) if i.item_key == "tile")
    assert money(tile.amount) == Dec("62584.43")
    # Every cost component points back at the exact rate record used.
    comps = db.scalars(select(m.CostComponent).where(m.CostComponent.estimate_item_id == tile.id)).all()
    assert all(c.source_ref for c in comps)
