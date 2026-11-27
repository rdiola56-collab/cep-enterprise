"""Adapters read files faithfully: exact units, no guessing, problems reported per row."""
import math
from decimal import Decimal as Dec

import pytest

from app.integrations import ADAPTERS, SourceError
from app.integrations.base import parse_measure

from . import import_samples as smp


def test_measure_text_formats():
    assert parse_measure("1,204.5 sf").unit == "ft2" and parse_measure("1,204.5 sf").value == Dec("1204.5")
    assert parse_measure("12'-6\"").value == Dec("12.5") and parse_measure("12'-6\"").unit == "ft"
    assert parse_measure("3'").value == 3
    assert parse_measure("40", "m").unit == "m"
    assert parse_measure("") is None
    with pytest.raises(ValueError, match="no unit"):
        parse_measure("40")
    with pytest.raises(ValueError, match="unknown unit"):
        parse_measure("12 furlongs")


def test_takeoff_csv_rows_keep_problems_and_units():
    src = ADAPTERS["takeoff_csv"](smp.takeoff_csv([
        ("A1", "Floor Tile", "Lobby", "L02", "1,076.39 sf"),
        ("A2", "Skirting", "Lobby", "L02", "12'-6\""),
        ("A3", "Floor Tile", "Bad", "L02", "12 furlongs"),
    ]))
    a1, a2, a3 = src.rows
    assert a1.measures["area"].unit == "ft2" and a1.level == "L02" and a1.source_ref == "A1"
    assert a2.measures["length"].value == Dec("12.5")
    assert a3.messages and not a3.measures  # kept and reported, not dropped
    assert not src.warnings
    assert ADAPTERS["takeoff_csv"](smp.takeoff_csv([("", "Floor Tile", "x", "L02", "1 m2")], with_id=False)).warnings
    with pytest.raises(SourceError, match="Subject"):
        ADAPTERS["takeoff_csv"](b"Label,Area\nx,1 m2\n")


def _plan(doc, ms):
    ms.add_line((0, 0), (3000, 4000), dxfattribs={"layer": "S-BEAM"})
    ms.add_lwpolyline([(0, 0), (20000, 0), (20000, 5000), (0, 5000)], close=True, dxfattribs={"layer": "A-FLOR-TILE"})
    ms.add_lwpolyline([(0, 0, 1), (2000, 0, 1)], format="xyb", close=True, dxfattribs={"layer": "A-ROUND"})  # a circle r=1 m
    ms.add_arc((0, 0), 1000, 0, 90, dxfattribs={"layer": "A-ARC"})
    blk = doc.blocks.new("DOOR-900")
    blk.add_line((0, 0), (900, 0))
    ms.add_blockref("DOOR-900", (5, 5), dxfattribs={"layer": "A-DOOR"})
    ms.add_text("note", dxfattribs={"layer": "A-ANNO"})


def test_dxf_measures_exactly_in_drawing_units():
    src = ADAPTERS["dxf"](smp.dxf(4, _plan), {"level": "L02"})
    by_layer = {r.group: r for r in src.rows}
    assert by_layer["S-BEAM"].measures["length"].value == 5000 and by_layer["S-BEAM"].measures["length"].unit == "mm"
    tile = by_layer["A-FLOR-TILE"].measures
    assert tile["area"].value == Dec(100_000_000) and tile["area"].unit == "mm2" and tile["length"].value == 50000
    assert abs(float(by_layer["A-ROUND"].measures["area"].value) - math.pi * 1e6) < 1e-6  # bulge arcs measured exactly
    assert abs(float(by_layer["A-ARC"].measures["length"].value) - math.pi / 2 * 1000) < 1e-6
    assert by_layer["A-DOOR"].label == "DOOR-900" and by_layer["A-DOOR"].measures["count"].value == 1
    assert all(r.level == "L02" for r in src.rows)
    assert src.info["skipped"] == {"TEXT": 1} and "1 TEXT" in src.warnings[0]  # reported, not silently lost


def test_dxf_without_units_is_refused_unless_the_user_states_them():
    with pytest.raises(SourceError, match="no units"):
        ADAPTERS["dxf"](smp.dxf(0, _plan))
    src = ADAPTERS["dxf"](smp.dxf(0, _plan), {"drawing_unit": "mm"})
    assert src.info["unit"] == "mm"
    with pytest.raises(SourceError, match="not a length"):
        ADAPTERS["dxf"](smp.dxf(0, _plan), {"drawing_unit": "kg"})
    with pytest.raises(SourceError):
        ADAPTERS["dxf"](b"this is not a drawing")


WALL = {"cls": "IfcWall", "name": "Wall 200", "storey": "Level 02",
        "qto": ("Qto_WallBaseQuantities", {"Length": 6000.0, "Height": 3000.0, "Width": 200.0, "NetSideArea": 18.0, "NetVolume": 3.6}),
        "pset": ("Pset_WallCommon", {"IsExternal": True})}


def test_ifc_quantities_are_converted_to_si_from_model_units():
    src = ADAPTERS["ifc"](smp.ifc([WALL, {"cls": "IfcDoor", "name": "D1"}, {"cls": "IfcOpeningElement"}]))
    assert len(src.rows) == 2  # openings are not priced elements
    wall, door = (next(r for r in src.rows if r.group == g) for g in ("IfcWall", "IfcDoor"))
    assert wall.group == "IfcWall" and wall.level == "Level 02"
    assert wall.measures["Length"].value == 6 and wall.measures["Length"].unit == "m"  # 6000 mm
    assert wall.measures["Qto_WallBaseQuantities.NetVolume"].value == Dec("3.6")
    assert wall.properties["Pset_WallCommon.IsExternal"] == "True"
    assert set(door.measures) == {"count"}
    assert src.info["without_quantities"] == 1 and any("no quantity sets" in w for w in src.warnings)


def test_ifc_imperial_model():
    beam = {"cls": "IfcBeam", "qto": ("Qto_BeamBaseQuantities", {"Length": 20.0, "NetVolume": 10.0})}
    (row,) = ADAPTERS["ifc"](smp.ifc([beam], length_unit="ft")).rows
    assert row.measures["Length"].value == Dec("6.096")  # 20 ft
    assert row.measures["NetVolume"].value == Dec("0.28316846592")  # 10 ft³, exact
    with pytest.raises(SourceError):
        ADAPTERS["ifc"](b"ISO-10303-21; garbage")
