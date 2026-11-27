"""Acceptance tests 8 and 9 (spec §12): drawing and BIM quantities priced through the one engine."""
from datetime import date
from decimal import Decimal as Dec

import pytest
from sqlalchemy import select

from app.db import models as m
from app.engine.precision import money
from app.services import estimates as est
from app.services import imports as imp
from app.services import projects as wbs

from . import import_samples as smp
from .db_seed import seed_master

AS_OF = date(2026, 11, 1)
TILE_100 = {"length": {"value": "20", "unit": "m"}, "width": {"value": "5", "unit": "m"}}  # 100 m²
BEAM_INPUTS = {"length": {"value": "6", "unit": "m"}, "width": {"value": "300", "unit": "mm"},
               "height": {"value": "600", "unit": "mm"}, "pieces": {"value": "10", "unit": "pcs"}}  # 10.8 m³


@pytest.fixture
def proj(db):
    seed_master(db)
    p = est.create_project(db, "TWR-A", "Tower A", "PHP")
    b = wbs.add_building(db, p.id, "T1", "Tower 1")
    floors = {code: wbs.add_floor(db, p.id, b.id, code, name, lvl)
              for code, name, lvl in (("L01", "Level 01", 1), ("L02", "Level 02", 2))}
    v = est.create_estimate(db, p.id, "EST", "Estimate", AS_OF)
    return p, v, floors


def rule(db, p, **kw):
    base = {"code": "R", "adapter": "takeoff_csv", "match_group": "Floor Tile*", "module": "tiling", "item_type": "floor_tile",
            "input_map": {"measured": "area"}}
    return imp.create_rule(db, p.id, {**base, **kw}, "est")


def manual_amount(db, v, key, **kw):
    return est.calculate_item(db, v.id, item_key=key, **kw).amount


# AT-8 drawing quantities ------------------------------------------------------------


def test_at8_takeoff_import_prices_like_a_manual_entry_and_reimport_updates(db, proj):
    p, v, floors = proj
    rule(db, p)
    rule(db, p, code="DOORS", match_group="Door", module="doors_windows_cabinets", item_type="door",
         input_map={"measured": "count"}, assembly_code=None)
    csv1 = smp.takeoff_csv([("A1", "Floor Tile 600", "Lobby", "Level 02", "100 m2"),
                            ("A2", "Floor Tile 600", "Corridor", "L01", "1,000 sf")])
    b = imp.stage(db, v.id, adapter="takeoff_csv", file_name="tiles.csv", content=csv1, source_name="A-201 Finishes",
                  source_revision="C", actor="est")
    assert b.summary["ready"] and b.summary["counts"] == {"mapped": 2}
    a1 = next(r for r in imp.batch_rows(db, b.id) if r.source_ref == "A1")
    assert a1.floor_id == floors["L02"].id  # page label matched to the WBS floor by name
    assert Dec(a1.preview["quantity"]) == 100 and a1.preview["unit"] == "m2"

    imp.commit(db, b.id, "est")
    items = {i.item_key: i for i in est.items(db, v.id)}
    assert set(items) == {"takeoff_csv:A-201-Finishes:A1", "takeoff_csv:A-201-Finishes:A2"}
    imported = items["takeoff_csv:A-201-Finishes:A1"]
    # The same 100 m² entered by hand costs exactly the same: one engine, one assembly, one price book.
    assert money(imported.amount) == money(manual_amount(db, v, "manual", module="tiling", item_type="floor_tile",
                                                         inputs=TILE_100)) == Dec("62584.43")
    assert imported.quantity_basis == "measured" and imported.floor_id == floors["L02"].id
    take = db.scalars(select(m.QuantityTakeoff).where(m.QuantityTakeoff.estimate_item_id == imported.id)).one()
    ref = db.get(m.DrawingReference, take.drawing_reference_id)
    assert take.source == "import:takeoff_csv" and ref.drawing_name == "A-201 Finishes" and ref.source_markup_id == "A1"
    assert db.get(m.FormulaVersion, take.formula_version_id) is not None
    est.remove_item(db, v.id, "manual")

    # Revision D: A1 re-measured at 120 m², A2 deleted from the drawing, A3 added.
    csv2 = smp.takeoff_csv([("A1", "Floor Tile 600", "Lobby", "Level 02", "120 m2"),
                            ("A3", "Door", "D-01", "Level 02", "")])
    b2 = imp.stage(db, v.id, adapter="takeoff_csv", file_name="tiles-D.csv", content=csv2, source_name="A-201 Finishes",
                   source_revision="D", actor="est")
    a3 = next(r for r in imp.batch_rows(db, b2.id) if r.source_ref == "A3")
    assert a3.status == "invalid"  # a door markup with no count: reported, never guessed
    assert b2.summary["stale_items"] == ["takeoff_csv:A-201-Finishes:A2"] and b2.summary["updated_items"] == 1
    with pytest.raises(imp.ImportBlocked, match="1 are invalid"):
        imp.commit(db, b2.id, "est")
    imp.set_excluded(db, b2.id, [a3.id], True, "door schedule priced separately", "est")
    imp.commit(db, b2.id, "est", remove_missing=True)
    items = {i.item_key: i for i in est.items(db, v.id)}
    assert set(items) == {"takeoff_csv:A-201-Finishes:A1"}  # updated in place, no duplicate; A2 removed on request
    assert items["takeoff_csv:A-201-Finishes:A1"].quantity == 120


def test_at8_dxf_layers_aggregate_per_floor_and_unmapped_layers_block_commit(db, proj):
    p, v, floors = proj
    rule(db, p, code="TILE-L", adapter="dxf", match_group="A-FLOR-TILE", aggregate=True)

    def plan(doc, ms):
        ms.add_lwpolyline([(0, 0), (10000, 0), (10000, 5000), (0, 5000)], close=True, dxfattribs={"layer": "A-FLOR-TILE"})
        ms.add_lwpolyline([(0, 0), (10000, 0), (10000, 5000), (0, 5000)], close=True, dxfattribs={"layer": "A-FLOR-TILE"})
        ms.add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-GRID"})

    b = imp.stage(db, v.id, adapter="dxf", file_name="L02.dxf", content=smp.dxf(4, plan), source_name="A-102",
                  source_revision="B", options={"level": "L02"}, actor="est")
    assert b.summary["counts"] == {"mapped": 2, "unmapped": 1} and not b.summary["ready"]
    with pytest.raises(imp.ImportBlocked, match="1 rows have no mapping rule"):
        imp.commit(db, b.id, "est")
    grid = next(r for r in imp.batch_rows(db, b.id) if r.group == "A-GRID")
    with pytest.raises(ValueError, match="reason"):
        imp.set_excluded(db, b.id, [grid.id], True, "", "est")
    imp.set_excluded(db, b.id, [grid.id], True, "grid lines are not work", "est")
    imp.commit(db, b.id, "est")
    (item,) = est.items(db, v.id)
    assert item.item_key == "dxf:A-102:TILE-L@L02" and item.quantity == 100  # 2 × 50 m², from mm²
    assert item.floor_id == floors["L02"].id
    assert money(item.amount) == Dec("62584.43")


def test_at8_validation_is_never_bypassed(db, proj):
    p, v, _ = proj
    # Wall tiling has no assembly in this library: the preview says so instead of making up a price.
    rule(db, p, code="WT", match_group="Wall Tile", item_type="wall_tile")
    b = imp.stage(db, v.id, adapter="takeoff_csv", file_name="t.csv", source_name="S", source_revision="1", actor="est",
                  content=smp.takeoff_csv([("1", "Wall Tile", "", "", "10 m2"), ("2", "Floor Tile", "", "", "12 furlongs")]))
    r1, r2 = imp.batch_rows(db, b.id)
    assert r1.status == "invalid" and "TILE-WALL300" in r1.messages[0]
    assert r2.status == "invalid" and "furlongs" in r2.messages[0]
    # A rule cannot map an area onto a count item, or sum dimensions.
    with pytest.raises(ValueError, match="aggregated"):
        rule(db, p, code="X", quantity_basis="formula", input_map={"length": "length", "width": "length"}, aggregate=True)
    with pytest.raises(ValueError, match="no input"):
        rule(db, p, code="Y", input_map={"area": "area"})
    # Approved estimates take no imports.
    est.calculate_item(db, v.id, item_key="manual", module="tiling", item_type="floor_tile", inputs=TILE_100)
    est.approve(db, v.id, "approver")
    with pytest.raises(est.EstimateLocked):
        imp.stage(db, v.id, adapter="takeoff_csv", file_name="t.csv", source_name="S", source_revision="2", actor="est",
                  content=smp.takeoff_csv([("1", "Floor Tile", "", "", "10 m2")]))


# AT-9 BIM quantities mapped to assemblies --------------------------------------------


def beam(guid, volume_m3, storey="Level 02"):
    return {"cls": "IfcBeam", "guid": guid, "storey": storey, "name": "B1 300x600",
            "qto": ("Qto_BeamBaseQuantities", {"Length": 6000.0, "NetVolume": volume_m3})}


def test_at9_bim_elements_map_to_assemblies_and_match_manual_pricing(db, proj):
    p, v, floors = proj
    imp.create_rule(db, p.id, {"code": "BEAMS", "adapter": "ifc", "match_group": "IfcBeam", "module": "reinforced_concrete",
                               "item_type": "beam", "input_map": {"measured": "NetVolume"}, "aggregate": True}, "est")
    imp.create_rule(db, p.id, {"code": "INT-WALL", "adapter": "ifc", "match_group": "IfcWall*",
                               "match_properties": {"Pset_WallCommon.IsExternal": "False"},
                               "module": "walls_partitions", "item_type": "chb_wall", "quantity_basis": "formula",
                               "input_map": {"length": "Length", "height": "Height"}}, "est")
    wall = {"cls": "IfcWall", "guid": "2O2Fr$t4X7Zf8NOew3FLOH", "storey": "Level 01",
            "qto": ("Qto_WallBaseQuantities", {"Length": 6000.0, "Height": 3000.0}),
            "pset": ("Pset_WallCommon", {"IsExternal": False})}
    ext = {**wall, "guid": "2O2Fr$t4X7Zf8NOew3FLOI", "pset": ("Pset_WallCommon", {"IsExternal": True})}
    model = smp.ifc([*(beam(f"1kTvXnbbzCWw8lcMd1dR4{i}", 1.08) for i in range(10)), wall, ext])
    b = imp.stage(db, v.id, adapter="ifc", file_name="tower.ifc", content=model, source_name="Tower A ARC+STR",
                  source_revision="R3", actor="est")
    assert b.summary["counts"] == {"mapped": 11, "unmapped": 1}  # the external wall has no rule yet
    imp.set_excluded(db, b.id, [r.id for r in imp.batch_rows(db, b.id, "unmapped")], True, "curtain wall package", "est")
    imp.commit(db, b.id, "est")
    items = {i.item_key: i for i in est.items(db, v.id)}
    beams = items["ifc:Tower-A-ARC-STR:BEAMS@L02"]
    assert beams.quantity == Dec("10.8") and beams.floor_id == floors["L02"].id
    hand = manual_amount(db, v, "manual-beam", module="reinforced_concrete", item_type="beam", inputs=BEAM_INPUTS)
    assert money(beams.amount) == money(hand)  # 10 modelled beams = the same beams measured by hand

    w = items["ifc:Tower-A-ARC-STR:2O2Fr-t4X7Zf8NOew3FLOH"]
    assert w.quantity_basis == "formula" and w.quantity == 18 and w.floor_id == floors["L01"].id
    take = db.scalars(select(m.QuantityTakeoff).where(m.QuantityTakeoff.estimate_item_id == w.id)).one()
    el = db.get(m.BimElement, take.bim_element_id)
    assert el.element_guid == "2O2Fr$t4X7Zf8NOew3FLOH" and el.model_revision == "R3" and take.source == "import:ifc"
    assert "[6 m]" in take.methodology  # 6000 mm in the model, traced through the WALL_AREA formula

    # A new revision recalculates measured and formula items alike, through the engine.
    v2 = est.new_revision(db, v.id)
    again = {i.item_key: i for i in est.items(db, v2.id)}
    assert again["ifc:Tower-A-ARC-STR:BEAMS@L02"].quantity_basis == "measured"
    assert again["ifc:Tower-A-ARC-STR:BEAMS@L02"].amount == beams.amount


def test_at9_elements_without_quantities_are_reported_not_invented(db, proj):
    p, v, _ = proj
    imp.create_rule(db, p.id, {"code": "BEAMS", "adapter": "ifc", "match_group": "IfcBeam", "module": "reinforced_concrete",
                               "item_type": "beam", "input_map": {"measured": "NetVolume"}}, "est")
    model = smp.ifc([{"cls": "IfcBeam", "guid": "1kTvXnbbzCWw8lcMd1dR4Z"}])
    b = imp.stage(db, v.id, adapter="ifc", file_name="m.ifc", content=model, source_name="M", source_revision="1", actor="est")
    (row,) = imp.batch_rows(db, b.id)
    assert row.status == "invalid" and "no 'NetVolume' measured" in row.messages[0]
    assert any("no quantity sets" in w for w in b.summary["warnings"])
