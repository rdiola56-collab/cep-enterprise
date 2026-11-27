"""IFC models (IFC2X3 / IFC4 / IFC4X3) via IfcOpenShell: element quantities for BIM mapping.

One row per physical element (walls, slabs, beams, columns, doors, pipes…),
keyed by its GlobalId:

    group       IFC class (IfcWall, IfcSlab, IfcDoor…)
    label       element type name, else the element name
    level       name of the containing storey, matched to a WBS floor
    measures    every quantity in its quantity sets (Qto_WallBaseQuantities.NetVolume and,
                where unambiguous, NetVolume), converted from the model's units to SI, plus count = 1
    properties  property-set values (Pset_WallCommon.IsExternal…), ObjectType, PredefinedType, Material

Quantities are read, never computed from geometry: a model exported without
base quantities gives rows with only a count, and mapping rules that need a
volume or area report them as invalid instead of inventing a value.
Revit models are imported through their IFC export (with "Export base quantities" on).
"""
from __future__ import annotations

import os
import tempfile
from collections import Counter
from decimal import Decimal

from .base import Measure, ParsedRow, ParsedSource, SourceError, to_decimal

QUANTITY_KINDS = {
    "IfcQuantityLength": ("LENGTHUNIT", "m"),
    "IfcQuantityArea": ("AREAUNIT", "m2"),
    "IfcQuantityVolume": ("VOLUMEUNIT", "m3"),
    "IfcQuantityWeight": ("MASSUNIT", "kg"),
    "IfcQuantityCount": (None, "pcs"),
}
NOT_PRICED = ("IfcOpeningElement", "IfcVirtualElement", "IfcFeatureElement", "IfcSpatialZone")


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    import ifcopenshell
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.unit as uu

    fd, tmp = tempfile.mkstemp(suffix=".ifc")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        try:
            model = ifcopenshell.open(tmp)
        except Exception as exc:
            raise SourceError(f"not a readable IFC file ({exc})") from exc
    finally:
        os.unlink(tmp)

    assigned = set()
    for ua in model.by_type("IfcUnitAssignment"):
        for u in ua.Units:
            if getattr(u, "UnitType", None):
                assigned.add(u.UnitType)
    if "LENGTHUNIT" not in assigned:
        raise SourceError("the model declares no length unit; it cannot be measured safely")
    scale: dict[str, Decimal] = {}
    approx: list[str] = []
    for unit_type in ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT", "MASSUNIT"):
        if unit_type in assigned:
            exact = _exact_scale(model, unit_type)
            if exact is None:
                f = to_decimal(float(uu.calculate_unit_scale(model, unit_type)))
                exact = f / 1000 if unit_type == "MASSUNIT" else f  # IfcOpenShell scales mass to grams
                approx.append(unit_type)
            scale[unit_type] = exact

    missing_units: Counter[str] = Counter()
    rows: list[ParsedRow] = []
    for el in model.by_type("IfcElement"):
        if any(el.is_a(c) for c in NOT_PRICED):
            continue
        etype = ue.get_type(el)
        container = ue.get_container(el)
        row = ParsedRow(
            source_ref=el.GlobalId,
            group=el.is_a(),
            label=(etype.Name if etype is not None and etype.Name else None) or el.Name or "",
            level=container.Name if container is not None and container.is_a("IfcBuildingStorey") else None,
        )
        row.measures["count"] = Measure(Decimal(1), "pcs")
        short: Counter[str] = Counter()
        found: dict[str, Measure] = {}
        for qto_name, q in _quantities(el, etype):
            kind = QUANTITY_KINDS.get(q.is_a())
            if kind is None:
                continue
            unit_type, si_unit = kind
            raw = q[3]
            if raw is None:
                continue
            if unit_type is not None and unit_type not in scale:
                missing_units[unit_type] += 1
                continue
            value = to_decimal(float(raw)) * (scale[unit_type] if unit_type else 1)
            found[f"{qto_name}.{q.Name}"] = Measure(value, si_unit)
            short[q.Name] += 1
        row.measures.update(found)
        for qualified, measure in found.items():
            name = qualified.split(".", 1)[1]
            if short[name] == 1:
                row.measures.setdefault(name, measure)

        props: dict[str, str] = {}
        for pset, values in ue.get_psets(el, psets_only=True).items():
            for k, v in values.items():
                if k != "id" and v is not None and not isinstance(v, (list, dict)):
                    props[f"{pset}.{k}"] = str(v)
        for attr in ("ObjectType", "PredefinedType", "Name", "Tag"):
            v = getattr(el, attr, None)
            if v not in (None, ""):
                props[attr] = str(v)
        if etype is not None and etype.Name:
            props["TypeName"] = etype.Name
        mats = ue.get_materials(el)
        if mats:
            props["Material"] = ", ".join(sorted({m.Name for m in mats if getattr(m, "Name", None)}))
        row.properties = props
        rows.append(row)

    warnings = [f"{u.lower()} converted with IfcOpenShell's floating-point factor (not a standard unit)" for u in approx]
    for unit_type, n in sorted(missing_units.items()):
        warnings.append(f"{n} quantities skipped: the model declares no {unit_type.lower().replace('unit', ' unit')}")
    counts = Counter(r.group for r in rows)
    info = {"schema": model.schema, "elements": len(rows), "classes": dict(sorted(counts.items())),
            "units": {k: format(v.normalize(), "f") for k, v in scale.items()},
            "without_quantities": sum(1 for r in rows if len(r.measures) == 1)}
    if info["without_quantities"]:
        warnings.append(f"{info['without_quantities']} element(s) have no quantity sets (export base quantities from the authoring tool)")
    return ParsedSource("ifc", rows, info=info, warnings=warnings)


SI_PREFIX = {None: 0, "EXA": 18, "PETA": 15, "TERA": 12, "GIGA": 9, "MEGA": 6, "KILO": 3, "HECTO": 2, "DECA": 1,
             "DECI": -1, "CENTI": -2, "MILLI": -3, "MICRO": -6, "NANO": -9}
SI_BASE = {"METRE": ("LENGTHUNIT", 1), "SQUARE_METRE": ("AREAUNIT", 2), "CUBIC_METRE": ("VOLUMEUNIT", 3), "GRAM": ("MASSUNIT", 1)}
CONVERSION_UNITS = {"foot": "ft", "inch": "in", "yard": "yd", "mile": "mi", "square foot": "ft2", "square inch": "in2",
                    "square yard": "yd2", "cubic foot": "ft3", "cubic inch": "in3", "cubic yard": "yd3", "pound": "lb"}


def _exact_scale(model, unit_type: str) -> Decimal | None:
    """Exact factor from the model's unit to SI (m, m2, m3, kg), for SI and the standard imperial units."""
    from ..engine.units import get_unit

    for ua in model.by_type("IfcUnitAssignment"):
        for u in ua.Units:
            if getattr(u, "UnitType", None) != unit_type:
                continue
            if u.is_a("IfcSIUnit"):
                base = SI_BASE.get(u.Name)
                if base is None or u.Prefix not in SI_PREFIX:
                    return None
                power = SI_PREFIX[u.Prefix] * base[1] - (3 if u.Name == "GRAM" else 0)  # kg is the SI mass unit
                return Decimal(10) ** power
            if u.is_a("IfcConversionBasedUnit"):
                sym = CONVERSION_UNITS.get((u.Name or "").strip().lower())
                return get_unit(sym).to_si if sym else None
            return None
    return None


def _quantities(el, etype):
    """Quantity sets on the element, then on its type (element values win)."""
    seen = set()
    sources = []
    for rel in getattr(el, "IsDefinedBy", None) or ():
        if rel.is_a("IfcRelDefinesByProperties"):
            sources.append(rel.RelatingPropertyDefinition)
    if etype is not None:
        sources.extend(getattr(etype, "HasPropertySets", None) or ())
    for d in sources:
        if d is None or not d.is_a("IfcElementQuantity"):
            continue
        for q in d.Quantities:
            key = (d.Name, q.Name)
            if key in seen:
                continue
            seen.add(key)
            yield d.Name, q
