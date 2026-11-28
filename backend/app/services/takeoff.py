"""PDF drawing sheets: upload, page scale, on-screen marks, and sending them to an estimate.

The PDF is kept exactly as uploaded. Pages are shown as images rendered by PDFium,
and the page's vector line work is offered for snapping. Measuring needs a scale on
the page; a mark on an unscaled page is shown without quantities and cannot be imported.
Sending marks to an estimate goes through the ordinary import pipeline (adapter
"pdf_takeoff"), so mapping rules, review and the all-or-nothing commit all apply.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..integrations import pdf_takeoff as pt
from ..integrations.base import SourceError, parse_measure
from .audit import audit

COORD = Decimal("0.001")  # points are kept to 1/1000 of a PDF point (about 0.35 micrometres of paper)
MAX_RENDER_PX = 8000
MAX_SNAPS = 60000
KINDS = ("length", "area", "count")


class TakeoffError(ValueError):
    pass


def _open(content: bytes):
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(content)
    except pdfium.PdfiumError as exc:
        raise SourceError(f"not a readable PDF ({exc})") from None


def upload(s: Session, project_id: uuid.UUID, *, file_name: str, content: bytes, set_name: str, revision: str,
           actor: str) -> m.DrawingSheet:
    if not set_name.strip() or not revision.strip():
        raise TakeoffError("name the drawing set and its revision: they identify the items when marks are sent to an estimate")
    if not content.startswith(b"%PDF"):
        raise SourceError("not a PDF file")
    pdf = _open(content)
    try:
        sheet = m.DrawingSheet(project_id=project_id, set_name=set_name.strip(), revision=revision.strip(), file_name=file_name,
                               file_sha256=hashlib.sha256(content).hexdigest(), content=content, page_count=len(pdf),
                               created_by=actor)
        s.add(sheet)
        s.flush()
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()  # as displayed, after the page's /Rotate
            label = ""
            try:
                label = pdf.get_page_label(i) or ""
            except Exception:  # noqa: BLE001  (labels are optional metadata)
                label = ""
            s.add(m.TakeoffPage(project_id=project_id, sheet_id=sheet.id, page_no=i + 1, width_pt=_q(w), height_pt=_q(h),
                                rotation=page.get_rotation(), label=label[:120]))
    finally:
        pdf.close()
    s.flush()
    audit(s, project_id, "drawing_sheets", sheet.id, "upload", actor,
          {"file": file_name, "sha256": sheet.file_sha256, "pages": sheet.page_count})
    return sheet


def _q(x) -> Decimal:
    return Decimal(repr(float(x))).quantize(COORD, ROUND_HALF_EVEN)


def get_sheet(s: Session, project_id: uuid.UUID, sheet_id: uuid.UUID) -> m.DrawingSheet:
    sheet = s.get(m.DrawingSheet, sheet_id)
    if sheet is None or sheet.project_id != project_id:
        raise LookupError("drawing not found")
    return sheet


def pages(s: Session, sheet_id: uuid.UUID) -> list[m.TakeoffPage]:
    return list(s.scalars(select(m.TakeoffPage).where(m.TakeoffPage.sheet_id == sheet_id).order_by(m.TakeoffPage.page_no)))


def get_page(s: Session, sheet: m.DrawingSheet, page_no: int) -> m.TakeoffPage:
    p = s.scalars(select(m.TakeoffPage).where(m.TakeoffPage.sheet_id == sheet.id, m.TakeoffPage.page_no == page_no)).first()
    if p is None:
        raise LookupError(f"page {page_no} not found")
    return p


def marks(s: Session, sheet_id: uuid.UUID) -> list[m.TakeoffMark]:
    return list(s.scalars(select(m.TakeoffMark).where(m.TakeoffMark.sheet_id == sheet_id).order_by(m.TakeoffMark.ref)))


# ------------------------------------------------------------------ pages


def set_label(s: Session, sheet: m.DrawingSheet, page_no: int, label: str, actor: str) -> m.TakeoffPage:
    p = get_page(s, sheet, page_no)
    old, p.label = p.label, label.strip()[:120]
    audit(s, sheet.project_id, "takeoff_pages", p.id, "label", actor, {"from": old, "to": p.label})
    return p


def set_scale(s: Session, sheet: m.DrawingSheet, page_no: int, data: dict, actor: str) -> m.TakeoffPage:
    """data: {"method": "stated", "paper": "1/4 in" | "0.25 in", "real": "1 ft"}
           or {"method": "measured", "p1": [x, y], "p2": [x, y], "length": "12'-6\""}"""
    p = get_page(s, sheet, page_no)
    method = data.get("method")
    try:
        if method == "stated":
            paper, real = _measure(data.get("paper"), "paper"), _measure(data.get("real"), "real")
            unit, upp = pt.scale_from_stated(paper, real)
            note = f"{data['paper'].strip()} = {data['real'].strip()}"
        elif method == "measured":
            p1, p2 = _point(data.get("p1")), _point(data.get("p2"))
            known = _measure(data.get("length"), "known length")
            unit, upp = pt.scale_from_measured(p1, p2, known)
            note = f"measured: {data['length'].strip()} between two picked points"
        else:
            raise TakeoffError("method must be 'stated' (a drawing scale) or 'measured' (a known dimension)")
    except ValueError as exc:
        raise TakeoffError(str(exc)) from None
    before = {"unit": p.scale_unit, "units_per_point": p.units_per_point, "note": p.scale_note}
    p.scale_unit, p.units_per_point, p.scale_method, p.scale_note = unit, format(upp.normalize(), "f"), method, note[:200]
    p.scaled_by, p.scaled_at = actor, datetime.now(timezone.utc)
    audit(s, sheet.project_id, "takeoff_pages", p.id, "scale", actor,
          {"before": before, "after": {"unit": unit, "units_per_point": p.units_per_point, "note": p.scale_note,
                                       "method": method, **({"p1": data["p1"], "p2": data["p2"]} if method == "measured" else {})}})
    return p


def _measure(text, what):
    if not isinstance(text, str) or not text.strip():
        raise TakeoffError(f"{what}: enter a length such as 0.25 in, 1 ft or 100 mm")
    text = text.strip()
    frac = text.split()
    if len(frac) == 2 and "/" in frac[0]:  # 1/4 in, 3/16 in
        a, b = frac[0].split("/", 1)
        try:
            text = f"{Decimal(a) / Decimal(b)} {frac[1]}"
        except Exception:  # noqa: BLE001
            raise TakeoffError(f"{what}: '{text}' is not a length") from None
    elif text.endswith('"') and "/" in text and "'" not in text:  # 1/4"
        a, b = text[:-1].split("/", 1)
        text = f"{Decimal(a) / Decimal(b)} in"
    elif text.endswith('"') and "'" not in text:
        text = f"{text[:-1]} in"
    try:
        msr = parse_measure(text)
    except ValueError as exc:
        raise TakeoffError(f"{what}: {exc}") from None
    if msr is None:
        raise TakeoffError(f"{what}: enter a length")
    return msr


def _point(p) -> list[str]:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise TakeoffError("a point is [x, y] in PDF points")
    try:
        return [format(_q(float(p[0])), "f"), format(_q(float(p[1])), "f")]
    except (TypeError, ValueError):
        raise TakeoffError("a point is [x, y] in PDF points") from None


# ------------------------------------------------------------------ marks


def _clean_points(page: m.TakeoffPage, points) -> list[list[str]]:
    if not isinstance(points, list) or len(points) > 5000:
        raise TakeoffError("points must be a list of at most 5000 [x, y] pairs")
    out = [_point(p) for p in points]
    w, h = Decimal(page.width_pt), Decimal(page.height_pt)
    for x, y in out:
        if not (Decimal(0) <= Decimal(x) <= w and Decimal(0) <= Decimal(y) <= h):
            raise TakeoffError("a point is outside the page")
    return out


def add_mark(s: Session, sheet: m.DrawingSheet, data: dict, actor: str) -> m.TakeoffMark:
    kind = data.get("kind")
    if kind not in KINDS:
        raise TakeoffError("kind must be length, area or count")
    subject = (data.get("subject") or "").strip()
    if not subject:
        raise TakeoffError("give the mark a subject (what it is, e.g. 'Partition type A'); mapping rules match it")
    page = get_page(s, sheet, int(data.get("page_no") or 0))
    pts = _clean_points(page, data.get("points"))
    _, problems = pt.measures(kind, pts, "m", "1")  # shape check only (needs no scale)
    if problems:
        raise TakeoffError(problems[0])
    ref = (s.scalar(select(func.max(m.TakeoffMark.ref)).where(m.TakeoffMark.sheet_id == sheet.id)) or 0) + 1
    mk = m.TakeoffMark(project_id=sheet.project_id, sheet_id=sheet.id, page_no=page.page_no, ref=ref, kind=kind,
                       subject=subject[:200], label=(data.get("label") or "").strip()[:300], points=pts, created_by=actor)
    s.add(mk)
    s.flush()
    audit(s, sheet.project_id, "takeoff_marks", mk.id, "create", actor, {"ref": ref, "kind": kind, "page": page.page_no})
    return mk


def update_mark(s: Session, sheet: m.DrawingSheet, mark_id: uuid.UUID, data: dict, actor: str) -> m.TakeoffMark:
    mk = s.get(m.TakeoffMark, mark_id)
    if mk is None or mk.sheet_id != sheet.id:
        raise LookupError("mark not found")
    before = {"subject": mk.subject, "label": mk.label, "points": mk.points}
    if "subject" in data:
        if not (data["subject"] or "").strip():
            raise TakeoffError("a mark needs a subject")
        mk.subject = data["subject"].strip()[:200]
    if "label" in data:
        mk.label = (data["label"] or "").strip()[:300]
    if "points" in data:
        pts = _clean_points(get_page(s, sheet, mk.page_no), data["points"])
        _, problems = pt.measures(mk.kind, pts, "m", "1")
        if problems:
            raise TakeoffError(problems[0])
        mk.points = pts
    audit(s, sheet.project_id, "takeoff_marks", mk.id, "update", actor, {"before": before})
    return mk


def delete_mark(s: Session, sheet: m.DrawingSheet, mark_id: uuid.UUID, actor: str) -> None:
    mk = s.get(m.TakeoffMark, mark_id)
    if mk is None or mk.sheet_id != sheet.id:
        raise LookupError("mark not found")
    audit(s, sheet.project_id, "takeoff_marks", mk.id, "delete", actor,
          {"ref": mk.ref, "kind": mk.kind, "subject": mk.subject, "points": mk.points})
    s.delete(mk)
    s.flush()


# ------------------------------------------------------------------ output


def page_dict(p: m.TakeoffPage) -> dict:
    return {"page_no": p.page_no, "width_pt": format(p.width_pt, "f"), "height_pt": format(p.height_pt, "f"),
            "rotation": p.rotation, "label": p.label, "scale": None if not p.units_per_point else {
                "unit": p.scale_unit, "units_per_point": p.units_per_point, "method": p.scale_method, "note": p.scale_note,
                "set_by": p.scaled_by, "set_at": p.scaled_at.isoformat() if p.scaled_at else None}}


def mark_dict(mk: m.TakeoffMark, page: m.TakeoffPage | None) -> dict:
    ms, problems = pt.measures(mk.kind, mk.points, page and page.scale_unit, page and page.units_per_point)
    return {"id": str(mk.id), "ref": f"M{mk.ref}", "page_no": mk.page_no, "kind": mk.kind, "subject": mk.subject,
            "label": mk.label, "points": mk.points, "measures": {k: x.to_dict() for k, x in ms.items()},
            "problems": problems, "created_by": mk.created_by}


def sheet_dict(s: Session, sheet: m.DrawingSheet, detail: bool = False) -> dict:
    out = {"id": str(sheet.id), "set_name": sheet.set_name, "revision": sheet.revision, "file_name": sheet.file_name,
           "file_sha256": sheet.file_sha256, "page_count": sheet.page_count, "created_by": sheet.created_by,
           "created_at": sheet.created_at.isoformat() if sheet.created_at else None}
    if detail:
        ps = pages(s, sheet.id)
        by_no = {p.page_no: p for p in ps}
        out["pages"] = [page_dict(p) for p in ps]
        out["marks"] = [mark_dict(mk, by_no.get(mk.page_no)) for mk in marks(s, sheet.id)]
    else:
        out["marks"] = s.scalar(select(func.count()).select_from(m.TakeoffMark).where(m.TakeoffMark.sheet_id == sheet.id))
    return out


def render_page(sheet: m.DrawingSheet, page: m.TakeoffPage, scale: float) -> bytes:
    """PNG of the page as displayed. scale = pixels per PDF point."""
    w, h = float(page.width_pt), float(page.height_pt)
    scale = max(0.1, min(scale, MAX_RENDER_PX / max(w, h)))
    pdf = _open(sheet.content)
    try:
        img = pdf[page.page_no - 1].render(scale=scale).to_pil()
    finally:
        pdf.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def snaps(sheet: m.DrawingSheet, page: m.TakeoffPage) -> dict:
    """Endpoints and corners of the page's vector line work, in displayed page points, for snapping."""
    import pdfplumber

    pts: set[tuple[float, float]] = set()
    with pdfplumber.open(io.BytesIO(sheet.content)) as pdf:
        # pdfplumber gives coordinates from the top-left of the page as displayed (its /Rotate applied),
        # the same frame PDFium renders in, so no further transform is needed.
        pg = pdf.pages[page.page_no - 1]
        for obj in [*pg.lines, *pg.rects, *pg.curves]:
            for x, y in obj.get("pts") or []:
                pts.add((round(float(x), 3), round(float(y), 3)))
            if len(pts) >= MAX_SNAPS:
                break
    out = [[x, y] for x, y in sorted(pts)]
    return {"points": out, "truncated": len(pts) >= MAX_SNAPS}


def export_document(s: Session, sheet: m.DrawingSheet) -> bytes:
    """The takeoff as the pdf_takeoff adapter reads it: pages with scales, and every mark."""
    doc = {"format": pt.FORMAT, "sheet_id": str(sheet.id), "file_name": sheet.file_name, "file_sha256": sheet.file_sha256,
           "pages": {str(p.page_no): {"label": p.label, "scale_unit": p.scale_unit, "units_per_point": p.units_per_point,
                                      "scale_note": p.scale_note} for p in pages(s, sheet.id)},
           "marks": [{"ref": mk.ref, "page_no": mk.page_no, "kind": mk.kind, "subject": mk.subject, "label": mk.label,
                      "points": mk.points} for mk in marks(s, sheet.id)]}
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def send_to_estimate(s: Session, sheet: m.DrawingSheet, version_id: uuid.UUID, actor: str, building_id: uuid.UUID | None = None):
    from . import imports as imp

    if not marks(s, sheet.id):
        raise TakeoffError("there are no marks on this drawing yet")
    return imp.stage(s, version_id, adapter="pdf_takeoff", file_name=sheet.file_name, content=export_document(s, sheet),
                     source_name=sheet.set_name, source_revision=sheet.revision, options={"sheet_id": str(sheet.id)},
                     building_id=building_id, actor=actor, project_id=sheet.project_id)
