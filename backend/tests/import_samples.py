"""Small, real source files built in code: a takeoff CSV, a DXF drawing and an IFC model."""
from __future__ import annotations

import io
import os
import tempfile


def takeoff_csv(rows: list[tuple[str, str, str, str, str]], with_id: bool = True) -> bytes:
    """rows: (id, subject, label, page label, measurement)"""
    head = "ID,Subject,Label,Page Label,Measurement\n" if with_id else "Subject,Label,Page Label,Measurement\n"
    lines = [head]
    for rid, subject, label, page, meas in rows:
        cells = ([rid] if with_id else []) + [subject, label, page, '"' + meas.replace('"', '""') + '"']
        lines.append(",".join(cells) + "\n")
    return "".join(lines).encode()


def dxf(units: int | None = 4, build=None) -> bytes:
    import ezdxf

    doc = ezdxf.new("R2010")
    if units is not None:
        doc.header["$INSUNITS"] = units
    else:
        doc.header["$INSUNITS"] = 0
    build(doc, doc.modelspace())
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode()


def ifc(elements: list[dict], length_unit: str = "mm") -> bytes:
    """elements: {"cls", "name", "storey", "qto": (name, {q: value}), "pset": (name, {p: value})}.
    Quantities are in the model's units: length in `length_unit` (mm or ft), area m2/ft2, volume m3/ft3."""
    import importlib

    from ifcopenshell import api

    for sub in ("aggregate", "project", "pset", "root", "spatial", "unit"):  # api submodules load on import
        importlib.import_module(f"ifcopenshell.api.{sub}")

    f = api.project.create_file(version="IFC4")
    project = api.root.create_entity(f, ifc_class="IfcProject", name="Sample")
    if length_unit == "mm":
        api.unit.assign_unit(f)  # millimetre, m2, m3
    else:
        units = [api.unit.add_conversion_based_unit(f, name=n) for n in ("foot", "square foot", "cubic foot")]
        api.unit.assign_unit(f, units=units)
    site = api.root.create_entity(f, ifc_class="IfcSite", name="Site")
    building = api.root.create_entity(f, ifc_class="IfcBuilding", name="Tower")
    api.aggregate.assign_object(f, relating_object=project, products=[site])
    api.aggregate.assign_object(f, relating_object=site, products=[building])
    storeys = {}
    for el in elements:
        name = el.get("storey", "Level 02")
        if name not in storeys:
            storeys[name] = api.root.create_entity(f, ifc_class="IfcBuildingStorey", name=name)
            api.aggregate.assign_object(f, relating_object=building, products=[storeys[name]])
        e = api.root.create_entity(f, ifc_class=el["cls"], name=el.get("name", el["cls"]))
        if el.get("guid"):
            e.GlobalId = el["guid"]
        api.spatial.assign_container(f, relating_structure=storeys[name], products=[e])
        if el.get("qto"):
            q = api.pset.add_qto(f, product=e, name=el["qto"][0])
            api.pset.edit_qto(f, qto=q, properties=el["qto"][1])
        if el.get("pset"):
            p = api.pset.add_pset(f, product=e, name=el["pset"][0])
            api.pset.edit_pset(f, pset=p, properties=el["pset"][1])
    fd, path = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)
    try:
        f.write(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)
