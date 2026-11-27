from decimal import Decimal as Dec

import pytest

from app.engine.units import Quantity, UnitError, convert, get_unit


@pytest.mark.parametrize(
    "value,src,dst,expected",
    [
        ("1", "ft", "m", "0.3048"),
        ("1", "in", "mm", "25.4"),
        ("1", "yd3", "m3", "0.764554857984"),
        ("1", "ft2", "m2", "0.09290304"),
        ("1", "lb", "kg", "0.45359237"),
        ("1", "t", "kg", "1000"),
        ("1000", "L", "m3", "1"),
        ("1", "gal", "L", "3.785411784"),
        ("1", "lb/ft", "kg/m", "1.488163943569553805774278215223097"),
        ("50", "%", "1", "0.5"),
    ],
)
def test_exact_conversions(value, src, dst, expected):
    assert convert(value, src, dst) == Dec(expected)


def test_round_trip_is_exact():
    assert convert(convert("123.456", "m", "ft"), "ft", "m") == Dec("123.456")


def test_aliases_and_case():
    assert get_unit("M²").symbol == "m2"
    assert get_unit("sq ft").symbol == "ft2"
    assert get_unit("cu.m.").symbol == "m3"


def test_incompatible_dimensions_rejected():
    with pytest.raises(UnitError):
        convert(1, "m", "m2")
    with pytest.raises(UnitError):
        Quantity.of(5, "kg").to("m3")


def test_unknown_unit():
    with pytest.raises(UnitError):
        get_unit("furlongs")
