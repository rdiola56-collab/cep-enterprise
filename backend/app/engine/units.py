"""Unit registry and dimensional quantities for the Universal Estimating Engine.

Every quantity in CEP is a :class:`Quantity`: a Decimal magnitude held in SI base
units plus a dimension vector. All conversion between metric and imperial units
goes through this module, so every estimating module gets identical results.

Conversion factors are exact definitions (e.g. 1 in = 0.0254 m exactly,
1 lb = 0.45359237 kg exactly), so no float rounding enters the engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Mapping

from .precision import D, ENGINE_PRECISION


class UnitError(ValueError):
    """Unknown unit or incompatible dimensions."""


# Dimension vector exponents: (Length, Mass, Time). Count, percent and other
# pure numbers are dimensionless (0, 0, 0).
@dataclass(frozen=True, slots=True)
class Dimension:
    length: int = 0
    mass: int = 0
    time: int = 0

    def __mul__(self, other: "Dimension") -> "Dimension":
        return Dimension(self.length + other.length, self.mass + other.mass, self.time + other.time)

    def __truediv__(self, other: "Dimension") -> "Dimension":
        return Dimension(self.length - other.length, self.mass - other.mass, self.time - other.time)

    def __pow__(self, n: int) -> "Dimension":
        return Dimension(self.length * n, self.mass * n, self.time * n)

    def root(self, n: int) -> "Dimension":
        if self.length % n or self.mass % n or self.time % n:
            raise UnitError(f"cannot take root {n} of dimension {self.describe()}")
        return Dimension(self.length // n, self.mass // n, self.time // n)

    @property
    def is_dimensionless(self) -> bool:
        return self == DIMENSIONLESS

    def describe(self) -> str:
        for name, dim in DIMENSION_KINDS.items():
            if dim == self:
                return name
        parts = []
        for sym, exp in (("L", self.length), ("M", self.mass), ("T", self.time)):
            if exp:
                parts.append(sym if exp == 1 else f"{sym}^{exp}")
        return "·".join(parts) or "dimensionless"


DIMENSIONLESS = Dimension()
LENGTH = Dimension(length=1)
AREA = Dimension(length=2)
VOLUME = Dimension(length=3)
MASS = Dimension(mass=1)
TIME = Dimension(time=1)
LINEAR_MASS = Dimension(length=-1, mass=1)  # kg/m: section weight, rebar unit weight
AREA_MASS = Dimension(length=-2, mass=1)  # kg/m2: sheet weight
DENSITY = Dimension(length=-3, mass=1)  # kg/m3

# Named kinds used by formula input/output declarations.
DIMENSION_KINDS: dict[str, Dimension] = {
    "dimensionless": DIMENSIONLESS,
    "length": LENGTH,
    "area": AREA,
    "volume": VOLUME,
    "mass": MASS,
    "time": TIME,
    "linear_mass": LINEAR_MASS,
    "area_mass": AREA_MASS,
    "density": DENSITY,
}


@dataclass(frozen=True, slots=True)
class Unit:
    symbol: str
    name: str
    dimension: Dimension
    to_si: Decimal  # multiply a value in this unit by to_si to get SI base units
    system: str  # "metric", "imperial", or "neutral"


_UNITS: dict[str, Unit] = {}
_ALIASES: dict[str, str] = {}


def _register(symbol: str, name: str, dim: Dimension, to_si: str | Decimal, system: str, *aliases: str) -> None:
    unit = Unit(symbol, name, dim, D(to_si), system)
    _UNITS[symbol] = unit
    for alias in (symbol, *aliases):
        _ALIASES[alias.lower()] = symbol


_IN = Decimal("0.0254")
_FT = Decimal("0.3048")
_YD = Decimal("0.9144")
_LB = Decimal("0.45359237")
_GAL = Decimal("0.003785411784")


def _div(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = ENGINE_PRECISION
        return a / b


# Dimensionless / count
_register("1", "unitless", DIMENSIONLESS, 1, "neutral", "", "unitless", "no", "nr")
_register("pcs", "pieces", DIMENSIONLESS, 1, "neutral", "pc", "piece", "pieces")
_register("ea", "each", DIMENSIONLESS, 1, "neutral", "each")
_register("set", "set", DIMENSIONLESS, 1, "neutral", "sets")
_register("lot", "lot", DIMENSIONLESS, 1, "neutral", "ls", "lump sum")
_register("bag", "bag", DIMENSIONLESS, 1, "neutral", "bags")
_register("roll", "roll", DIMENSIONLESS, 1, "neutral", "rolls")
_register("%", "percent", DIMENSIONLESS, "0.01", "neutral", "pct", "percent")
# Length
_register("mm", "millimetre", LENGTH, "0.001", "metric")
_register("cm", "centimetre", LENGTH, "0.01", "metric")
_register("m", "metre", LENGTH, 1, "metric", "lm", "meter", "metre", "l.m.")
_register("km", "kilometre", LENGTH, 1000, "metric")
_register("in", "inch", LENGTH, _IN, "imperial", "inch", "inches", '"')
_register("ft", "foot", LENGTH, _FT, "imperial", "foot", "feet", "lf", "'")
_register("yd", "yard", LENGTH, _YD, "imperial", "yard", "yards")
# Area
_register("mm2", "square millimetre", AREA, "0.000001", "metric", "mm²")
_register("cm2", "square centimetre", AREA, "0.0001", "metric", "cm²")
_register("m2", "square metre", AREA, 1, "metric", "m²", "sqm", "sq.m.")
_register("ha", "hectare", AREA, 10000, "metric")
_register("in2", "square inch", AREA, _IN**2, "imperial", "in²", "sq in")
_register("ft2", "square foot", AREA, _FT**2, "imperial", "ft²", "sf", "sq ft", "sqft")
_register("yd2", "square yard", AREA, _YD**2, "imperial", "yd²", "sy", "sq yd")
# Volume
_register("cm3", "cubic centimetre", VOLUME, "0.000001", "metric", "cm³", "cc")
_register("L", "litre", VOLUME, "0.001", "metric", "l", "liter", "litre", "liters", "litres")
_register("m3", "cubic metre", VOLUME, 1, "metric", "m³", "cum", "cu.m.")
_register("in3", "cubic inch", VOLUME, _IN**3, "imperial", "in³")
_register("ft3", "cubic foot", VOLUME, _FT**3, "imperial", "ft³", "cf", "cu ft")
_register("yd3", "cubic yard", VOLUME, _YD**3, "imperial", "yd³", "cy", "cu yd")
_register("gal", "US gallon", VOLUME, _GAL, "imperial", "gallon", "gallons")
# Mass
_register("g", "gram", MASS, "0.001", "metric")
_register("kg", "kilogram", MASS, 1, "metric", "kgs", "kilogram")
_register("t", "tonne", MASS, 1000, "metric", "tonne", "mt", "metric ton")
_register("lb", "pound", MASS, _LB, "imperial", "lbs", "pound", "pounds")
_register("ton", "short ton", MASS, _LB * 2000, "imperial", "short ton", "us ton")
# Time
_register("s", "second", TIME, 1, "neutral")
_register("min", "minute", TIME, 60, "neutral")
_register("hr", "hour", TIME, 3600, "neutral", "h", "hour", "hours", "hrs")
_register("day", "calendar day (24 h)", TIME, 86400, "neutral", "days")
# Linear mass (section weight, rebar unit weight)
_register("kg/m", "kilogram per metre", LINEAR_MASS, 1, "metric")
_register("lb/ft", "pound per foot", LINEAR_MASS, _div(_LB, _FT), "imperial", "plf")
# Area mass
_register("kg/m2", "kilogram per square metre", AREA_MASS, 1, "metric", "kg/m²")
_register("lb/ft2", "pound per square foot", AREA_MASS, _div(_LB, _FT**2), "imperial", "psf")
# Density
_register("kg/m3", "kilogram per cubic metre", DENSITY, 1, "metric", "kg/m³")
_register("lb/ft3", "pound per cubic foot", DENSITY, _div(_LB, _FT**3), "imperial", "pcf")


def get_unit(symbol: str) -> Unit:
    key = _ALIASES.get(symbol.strip().lower())
    if key is None:
        raise UnitError(f"unknown unit '{symbol}'")
    return _UNITS[key]


def all_units() -> list[Unit]:
    return list(_UNITS.values())


def convert(value: Decimal | int | str, from_unit: str, to_unit: str) -> Decimal:
    src, dst = get_unit(from_unit), get_unit(to_unit)
    if src.dimension != dst.dimension:
        raise UnitError(
            f"cannot convert {src.symbol} ({src.dimension.describe()}) to {dst.symbol} ({dst.dimension.describe()})"
        )
    with localcontext() as ctx:
        ctx.prec = ENGINE_PRECISION
        return D(value) * src.to_si / dst.to_si


@dataclass(frozen=True, slots=True)
class Quantity:
    """A magnitude in SI base units with its dimension."""

    si: Decimal
    dimension: Dimension

    @classmethod
    def of(cls, value: Decimal | int | str, unit: str) -> "Quantity":
        u = get_unit(unit)
        with localcontext() as ctx:
            ctx.prec = ENGINE_PRECISION
            return cls(D(value) * u.to_si, u.dimension)

    @classmethod
    def number(cls, value: Decimal | int | str) -> "Quantity":
        return cls(D(value), DIMENSIONLESS)

    def to(self, unit: str) -> Decimal:
        u = get_unit(unit)
        if u.dimension != self.dimension:
            raise UnitError(
                f"quantity is {self.dimension.describe()}, cannot express in {u.symbol} ({u.dimension.describe()})"
            )
        with localcontext() as ctx:
            ctx.prec = ENGINE_PRECISION
            return self.si / u.to_si


def check_unit_kind(unit: str, kind: str) -> Unit:
    u = get_unit(unit)
    expected = DIMENSION_KINDS.get(kind)
    if expected is None:
        raise UnitError(f"unknown dimension kind '{kind}'")
    if u.dimension != expected:
        raise UnitError(f"unit '{unit}' is {u.dimension.describe()}, expected {kind}")
    return u


def units_by_kind() -> Mapping[str, list[str]]:
    out: dict[str, list[str]] = {}
    for u in _UNITS.values():
        out.setdefault(u.dimension.describe(), []).append(u.symbol)
    return out
