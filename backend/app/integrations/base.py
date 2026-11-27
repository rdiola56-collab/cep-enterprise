"""Common shape for drawing and BIM quantity sources.

An adapter only *reads* a file and reports what it measured, with units. It
never prices anything, never picks an assembly and never writes to an
estimate: mapping rules decide what a row means, and every quantity is then
priced through ``EstimatingEngine.calculate_item`` like a manual entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ..engine.units import UnitError, get_unit


class SourceError(ValueError):
    """The whole file cannot be read (wrong format, no units, corrupt)."""


@dataclass
class Measure:
    value: Decimal
    unit: str  # a unit symbol from the engine's registry

    def to_dict(self) -> dict:
        return {"value": format(self.value.normalize(), "f"), "unit": self.unit}


@dataclass
class ParsedRow:
    source_ref: str  # stable id inside the source: markup id, entity handle, IFC GlobalId
    group: str  # what mapping rules match first: subject, layer, IFC class
    label: str = ""
    level: str | None = None  # page label / storey name: matched to a WBS floor by name
    measures: dict[str, Measure] = field(default_factory=dict)
    properties: dict[str, str] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)  # problems reading this row: it cannot be imported


@dataclass
class ParsedSource:
    adapter: str
    rows: list[ParsedRow]
    info: dict = field(default_factory=dict)  # file-level facts: units, schema, skipped entity counts
    warnings: list[str] = field(default_factory=list)


def to_decimal(value) -> Decimal:
    if isinstance(value, float):
        # Drawing coordinates are binary floats; repr() is the shortest exact round-trip text.
        return Decimal(repr(float(value)))
    return Decimal(str(value))


_FT_IN = re.compile(r"""^\s*(?P<ft>-?\d+(?:\.\d+)?)\s*'\s*-?\s*(?:(?P<inch>\d+(?:\.\d+)?)\s*(?:"|'')?)?\s*$""")
_NUM_UNIT = re.compile(r"^\s*(?P<num>[-+]?[\d,]*\.?\d+(?:[eE][-+]?\d+)?)\s*(?P<unit>.*?)\s*$")


def parse_measure(text: str, default_unit: str | None = None) -> Measure | None:
    """'12.5 m', '1,204.3 sf', "12'-6\"", '40' (with a default unit). None for an empty cell.
    Raises ValueError for text that is not a number with a known unit."""
    text = (text or "").strip()
    if not text:
        return None
    m = _FT_IN.match(text)
    if m:
        ft = Decimal(m.group("ft"))
        inch = Decimal(m.group("inch") or 0)
        return Measure(ft + (inch / 12 if ft >= 0 else -inch / 12), "ft")
    m = _NUM_UNIT.match(text)
    if not m:
        raise ValueError(f"'{text}' is not a measurement")
    try:
        num = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"'{text}' is not a measurement") from exc
    unit = m.group("unit") or default_unit
    if not unit:
        raise ValueError(f"'{text}' has no unit")
    try:
        sym = get_unit(unit).symbol
    except (UnitError, KeyError) as exc:
        raise ValueError(f"'{text}': unknown unit '{unit}'") from exc
    return Measure(num, sym)
