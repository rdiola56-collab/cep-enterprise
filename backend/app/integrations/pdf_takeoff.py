"""On-screen takeoff from PDF drawings.

The measuring itself happens in CEP's drawing screen: a page is given a scale
(a stated drawing scale such as 1/4" = 1'-0", or a known dimension measured on the
page), then lengths, areas and counts are drawn on it. This adapter reads the
takeoff document the server writes from those marks (JSON, see
services/takeoff.export_document) and turns each mark into a row, so marks go
through the same mapping rules, review and all-or-nothing commit as any import.

All arithmetic is decimal. Points are PDF points (1/72 inch of paper); a scale
is "real length per point" in a stated unit.
"""
from __future__ import annotations

import json
from decimal import Decimal, localcontext

from ..engine.units import get_unit
from .base import Measure, ParsedRow, ParsedSource, SourceError

AREA_UNIT = {"in": "in2", "ft": "ft2", "yd": "yd2", "mm": "mm2", "cm": "cm2", "m": "m2"}
POINTS_PER_INCH = Decimal(72)
FORMAT = "cep-pdf-takeoff/1"


def _dist(a, b) -> Decimal:
    dx, dy = Decimal(b[0]) - Decimal(a[0]), Decimal(b[1]) - Decimal(a[1])
    with localcontext() as ctx:
        ctx.prec = 34
        return (dx * dx + dy * dy).sqrt()


def path_points(points) -> Decimal:
    """Length of an open polyline, in PDF points."""
    return sum((_dist(a, b) for a, b in zip(points, points[1:])), Decimal(0))


def polygon_points2(points) -> Decimal:
    """Enclosed area of a closed polygon (shoelace), in square PDF points."""
    n = len(points)
    twice = sum((Decimal(points[i][0]) * Decimal(points[(i + 1) % n][1]) - Decimal(points[(i + 1) % n][0]) * Decimal(points[i][1])
                 for i in range(n)), Decimal(0))
    return abs(twice) / 2


def measures(kind: str, points: list, unit: str | None, units_per_point: str | None) -> tuple[dict[str, Measure], list[str]]:
    """What a mark measures, in the page's scale unit. Returns (measures, problems)."""
    problems = []
    if kind == "count":
        if not points:
            problems.append("a count mark needs at least one point")
        return {"count": Measure(Decimal(len(points)), "pcs")}, problems
    if kind == "length" and len(points) < 2:
        return {}, ["a length needs at least two points"]
    if kind == "area" and len(points) < 3:
        return {}, ["an area needs at least three points"]
    if not unit or not units_per_point:
        return {}, ["the page has no scale yet; set the scale before measuring"]
    k = Decimal(units_per_point)
    if kind == "length":
        return {"length": Measure(path_points(points) * k, unit), "count": Measure(Decimal(1), "pcs")}, problems
    closed = [*points, points[0]]
    return {"area": Measure(polygon_points2(points) * k * k, AREA_UNIT[unit]),
            "perimeter": Measure(path_points(closed) * k, unit),
            "length": Measure(path_points(closed) * k, unit),
            "count": Measure(Decimal(1), "pcs")}, problems


def scale_from_stated(paper, real) -> tuple[str, Decimal]:
    """paper and real are Measures, e.g. 0.25 in = 1 ft, or 1 mm = 100 mm (1:100). Returns (unit, real per point)."""
    for what, m in (("paper", paper), ("real", real)):
        if get_unit(m.unit).dimension.describe() != "length" or m.value <= 0:
            raise ValueError(f"the {what} side of the scale must be a positive length")
    from ..engine.units import convert

    paper_pt = convert(paper.value, paper.unit, "in") * POINTS_PER_INCH
    unit = _scale_unit(real.unit)
    return unit, convert(real.value, real.unit, unit) / paper_pt


def scale_from_measured(p1, p2, known) -> tuple[str, Decimal]:
    if get_unit(known.unit).dimension.describe() != "length" or known.value <= 0:
        raise ValueError("the known dimension must be a positive length")
    d = _dist(p1, p2)
    if d < Decimal("0.5"):
        raise ValueError("pick two points further apart to set the scale")
    from ..engine.units import convert

    unit = _scale_unit(known.unit)
    return unit, convert(known.value, known.unit, unit) / d


def _scale_unit(symbol: str) -> str:
    unit = get_unit(symbol).symbol
    if unit not in AREA_UNIT:
        raise ValueError(f"use in, ft, yd, mm, cm or m for a drawing scale (not {unit})")
    return unit


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    try:
        doc = json.loads(content)
    except ValueError:
        raise SourceError("not a CEP takeoff document") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise SourceError("not a CEP takeoff document (send marks to an estimate from the drawing screen)")
    pages = {int(k): v for k, v in doc.get("pages", {}).items()}
    rows = []
    for mk in doc.get("marks", []):
        page = pages.get(int(mk["page_no"]), {})
        ms, problems = measures(mk["kind"], mk["points"], page.get("scale_unit"), page.get("units_per_point"))
        row = ParsedRow(source_ref=f"M{mk['ref']}", group=mk["subject"], label=mk.get("label") or "",
                        level=(page.get("label") or None), measures=ms,
                        properties={"kind": mk["kind"], "page": str(mk["page_no"]),
                                    **({"scale": page["scale_note"]} if page.get("scale_note") else {})},
                        messages=[f"page {mk['page_no']}: {p}" for p in problems])
        rows.append(row)
    unscaled = sorted({int(mk["page_no"]) for mk in doc.get("marks", []) if mk["kind"] != "count"
                       and not pages.get(int(mk["page_no"]), {}).get("units_per_point")})
    warnings = [f"page(s) {', '.join(map(str, unscaled))} have marks but no scale"] if unscaled else []
    return ParsedSource("pdf_takeoff", rows, info={"sheet": doc.get("file_name"), "file_sha256": doc.get("file_sha256"),
                                                   "pages": len(pages), "marks": len(rows)}, warnings=warnings)
