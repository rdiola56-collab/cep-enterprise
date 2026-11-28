"""Small, real source files built in code: a takeoff CSV, DXF and DWG drawings, an IFC model and a PDF plan."""
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


def dwg(dxf_bytes: bytes, version: str = "AC1018") -> bytes:
    """The same drawing written as DWG by an independent library (acad-ts, a port of ACadSharp), so the
    DWG reader (LibreDWG) is tested on DWG bytes it did not write itself."""
    import pathlib
    import shutil
    import subprocess

    tool = pathlib.Path(__file__).resolve().parents[1] / "tools" / "dwg" / "make_sample.mjs"
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "in.dxf"), os.path.join(d, "out.dwg")
        with open(src, "wb") as fh:
            fh.write(dxf_bytes)
        subprocess.run([shutil.which("node") or "node", str(tool), src, out, version], check=True, capture_output=True,
                       timeout=120)
        with open(out, "rb") as fh:
            return fh.read()


def pdf_plan(pages: list[dict]) -> bytes:
    """A vector PDF. pages: {"size": (w, h) in points, "lines": [((x1, y1), (x2, y2))] from the TOP-LEFT corner,
    "rotate": 0|90|180|270 (the page's /Rotate, as a CAD plot of a landscape sheet on portrait paper has)}."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for p in pages:
        w, h = p.get("size", (1224, 792))
        c.setPageSize((w, h))
        c.setLineWidth(1)
        for (x1, y1), (x2, y2) in p.get("lines", []):
            c.line(x1, h - y1, x2, h - y2)
        c.setFont("Helvetica", 10)
        c.drawString(36, 36, p.get("title", "PLAN  SCALE 1/4\" = 1'-0\""))
        c.showPage()
    c.save()
    reader, writer = PdfReader(io.BytesIO(buf.getvalue())), PdfWriter()
    for page, spec in zip(reader.pages, pages):
        if spec.get("rotate"):
            page.rotate(spec["rotate"])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
