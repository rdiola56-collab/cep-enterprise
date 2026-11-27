"""Takeoff CSV: a Bluebeam Revu "Markups Summary" export, or any CSV with the same columns.

Recognised columns (case-insensitive): Subject, Label, Page Label, Layer, ID,
Measurement, Length, Area, Volume, Wall Area, Depth, Count, Comments, Space.
Measured cells must carry their unit ("12.5 m", "1,204 sf", "12'-6\"").
A row with an unreadable measurement is kept and reported, never dropped.
"""
from __future__ import annotations

import csv
import io

from ..engine.units import get_unit
from .base import ParsedRow, ParsedSource, SourceError, parse_measure

MEASURE_COLUMNS = {
    "measurement": "measurement",
    "length": "length",
    "area": "area",
    "volume": "volume",
    "wall area": "wall_area",
    "depth": "depth",
    "count": "count",
}
PROPERTY_COLUMNS = ("layer", "comments", "space", "author", "status", "color")


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    options = options or {}
    default_unit = options.get("default_unit") or None
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SourceError("the file has no header row")
    cols = {name.strip().lower(): name for name in reader.fieldnames if name}
    if "subject" not in cols:
        raise SourceError("no 'Subject' column: export the Markups Summary as CSV with the Subject column")
    if not any(c in cols for c in MEASURE_COLUMNS):
        raise SourceError("no measurement column (Measurement, Length, Area, Volume or Count)")
    has_id = "id" in cols

    rows: list[ParsedRow] = []
    for n, rec in enumerate(reader, start=1):
        get = lambda key: (rec.get(cols[key]) or "").strip() if key in cols else ""  # noqa: E731
        row = ParsedRow(
            source_ref=get("id") or f"row-{n}",
            group=get("subject"),
            label=get("label"),
            level=get("page label") or None,
            properties={k: get(k) for k in PROPERTY_COLUMNS if get(k)},
        )
        if not row.group:
            row.messages.append("no Subject")
        for col, name in MEASURE_COLUMNS.items():
            if col not in cols:
                continue
            try:
                value = parse_measure(get(col), "pcs" if name == "count" else default_unit)
            except ValueError as exc:
                row.messages.append(f"{cols[col]}: {exc}")
                continue
            if value is None:
                continue
            row.measures[name] = value
            if name == "measurement":
                # Bluebeam's Measurement column holds whichever kind the markup measures.
                row.measures.setdefault(_kind(value.unit), value)
        if not row.measures and not row.messages:
            row.messages.append("no measured value")
        rows.append(row)

    warnings = []
    if not has_id:
        warnings.append("no ID column: rows are keyed by position, so re-importing a revised file may not match earlier rows")
    return ParsedSource("takeoff_csv", rows, info={"columns": list(reader.fieldnames), "rows": len(rows)}, warnings=warnings)


def _kind(unit: str) -> str:
    dim = get_unit(unit).dimension.describe()
    return {"dimensionless": "count"}.get(dim, dim)
