"""DXF drawings (AutoCAD and compatible): lengths, areas and counts by layer.

Each supported entity becomes one row, keyed by its DXF handle:

    LINE, ARC              length
    LWPOLYLINE, POLYLINE   length; area too when closed
    CIRCLE, ELLIPSE        length (circumference) and area
    INSERT (block)         count 1; group = layer, label = block name, attributes as properties

The drawing unit comes from $INSUNITS. A unitless drawing is refused unless
the user states its unit: the importer never guesses a scale. Other entity
types (text, dimensions, hatches…) are counted and reported, not silently lost.
DWG files are read by the DWG adapter, which converts them to DXF and calls this one.
"""
from __future__ import annotations

import math
import os
import tempfile
from collections import Counter
from decimal import Decimal

from ..engine.units import get_unit
from .base import Measure, ParsedRow, ParsedSource, SourceError, to_decimal

INSUNITS = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m", 10: "yd", 3: "mi", 7: "km"}
FLATTEN_TOLERANCE = 1e-6  # drawing units; arcs and bulges are measured to this chord deviation


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    import ezdxf
    from ezdxf import path as ezpath
    from ezdxf.math import area as poly_area  # ellipses only (flattened)

    options = options or {}
    fd, tmp = tempfile.mkstemp(suffix=".dxf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        try:
            doc = ezdxf.readfile(tmp)
        except (OSError, ezdxf.DXFError) as exc:
            raise SourceError(f"not a readable DXF file ({exc})") from exc
    finally:
        os.unlink(tmp)

    code = doc.header.get("$INSUNITS", 0)
    declared = INSUNITS.get(code)
    override = options.get("drawing_unit") or None
    if override:
        unit = get_unit(override).symbol
        if get_unit(unit).dimension.describe() != "length":
            raise SourceError(f"drawing unit '{override}' is not a length unit")
    elif declared:
        unit = declared
    else:
        raise SourceError("the drawing has no units ($INSUNITS is unset); state the drawing unit (e.g. mm or ft) to import it")
    area_unit = {"in": "in2", "ft": "ft2", "mm": "mm2", "cm": "cm2", "m": "m2", "yd": "yd2", "km": "km2", "mi": "mi2"}.get(unit)
    if area_unit is None:
        raise SourceError(f"unsupported drawing unit '{unit}'")

    level = options.get("level") or None
    skipped: Counter[str] = Counter()
    rows: list[ParsedRow] = []

    def add(entity, length=None, area=None, count=None, label="", props=None):
        row = ParsedRow(source_ref=entity.dxf.handle, group=entity.dxf.layer, label=label, level=level,
                        properties={"entity": entity.dxftype(), **(props or {})})
        if length is not None:
            row.measures["length"] = Measure(_dec(length), unit)
        if area is not None:
            row.measures["area"] = Measure(_dec(area), area_unit)
        row.measures["count"] = Measure(Decimal(count if count is not None else 1), "pcs")
        rows.append(row)

    for e in doc.modelspace():
        kind = e.dxftype()
        try:
            if kind == "LINE":
                add(e, length=(e.dxf.end - e.dxf.start).magnitude)
            elif kind == "ARC":
                span = (e.dxf.end_angle - e.dxf.start_angle) % 360 or 360
                add(e, length=e.dxf.radius * math.radians(span))
            elif kind == "LWPOLYLINE":
                pts = [(x, y, b) for x, y, b in e.get_points("xyb")]
                length, area = _bulge_polyline(pts, e.closed)
                add(e, length=length, area=area if e.closed else None)
            elif kind == "POLYLINE":
                if e.is_2d_polyline:
                    pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.get("bulge", 0)) for v in e.vertices]
                    length, area = _bulge_polyline(pts, e.is_closed)
                    add(e, length=length, area=area if e.is_closed else None)
                elif e.is_3d_polyline:
                    add(e, length=_path_length(e, ezpath))  # 3D polylines have no bulges and no plan area
                else:
                    skipped["POLYLINE (mesh)"] += 1
            elif kind == "CIRCLE":
                r = e.dxf.radius
                add(e, length=2 * math.pi * r, area=math.pi * r * r)
            elif kind == "ELLIPSE":
                pts = list(ezpath.make_path(e).flattening(FLATTEN_TOLERANCE))
                full = abs(e.dxf.end_param - e.dxf.start_param - 2 * math.pi) < 1e-9
                add(e, length=_path_length(e, ezpath), area=abs(poly_area(pts)) if full else None)
            elif kind == "INSERT":
                attribs = {a.dxf.tag: a.dxf.text for a in e.attribs}
                add(e, label=e.dxf.name, props={"block": e.dxf.name, **attribs})
            else:
                skipped[kind] += 1
        except Exception as exc:  # one bad entity must not hide the rest; it is reported as invalid
            row = ParsedRow(source_ref=e.dxf.handle, group=e.dxf.get("layer", "0"), level=level, properties={"entity": kind})
            row.messages.append(f"could not measure {kind}: {exc}")
            rows.append(row)

    warnings = []
    if override and declared and get_unit(override).symbol != declared:
        warnings.append(f"drawing declares {declared} but was imported as {unit} at the user's request")
    if skipped:
        warnings.append("not measured: " + ", ".join(f"{n} {k}" for k, n in sorted(skipped.items())))
    return ParsedSource("dxf", rows, info={"unit": unit, "declared_unit": declared, "dxf_version": doc.dxfversion,
                                           "skipped": dict(skipped), "rows": len(rows)}, warnings=warnings)


def _bulge_polyline(pts: list[tuple[float, float, float]], closed: bool) -> tuple[float, float]:
    """Exact length and enclosed area of a 2D polyline whose segments may be circular arcs (bulges)."""
    n = len(pts)
    segs = n if closed else n - 1
    length = 0.0
    signed = 0.0
    for i in range(segs):
        x1, y1, b = pts[i]
        x2, y2, _ = pts[(i + 1) % n]
        chord = math.hypot(x2 - x1, y2 - y1)
        signed += (x1 * y2 - x2 * y1) / 2
        if b and chord:
            theta = 4 * math.atan(b)  # included angle; sign = arc direction
            r = chord / (2 * math.sin(abs(theta) / 2))
            length += r * abs(theta)
            signed += math.copysign(r * r / 2 * (abs(theta) - math.sin(abs(theta))), b)
        else:
            length += chord
    return length, abs(signed)


def _path_length(entity, ezpath) -> float:
    pts = list(ezpath.make_path(entity).flattening(FLATTEN_TOLERANCE))
    return sum((b - a).magnitude for a, b in zip(pts, pts[1:]))


def _dec(x: float) -> Decimal:
    # Coordinates are floats; keep 9 decimals of the drawing unit (well below any drafting tolerance).
    return to_decimal(round(x, 9))
