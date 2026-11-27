"""Estimate reports: one data assembly, rendered to Excel (openpyxl) or PDF (ReportLab).

Both outputs come from the same ``estimate_report_data`` so the Excel workbook,
the PDF and the API always show the same figures. Amounts are rounded only
here, at presentation, using the project's money precision.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from ..db import models as m
from ..engine.precision import money
from . import estimates as est

MARKUP_ORDER = ("overhead", "contingency", "profit", "tax")


def estimate_report_data(s: Session, version_id: uuid.UUID) -> dict:
    v = s.get(m.EstimateVersion, version_id)
    if v is None:
        raise LookupError("estimate version not found")
    e = s.get(m.EstimateRecord, v.estimate_id)
    p = s.get(m.Project, v.project_id)
    items = est.items(s, version_id)
    totals = est.cost_summary(s, version_id)
    markups: list[tuple[str, str, Decimal]] = []
    seen: dict[str, str] = {}
    for it in items:
        for mk in it.breakdown["markups"]:
            seen.setdefault(mk["code"], f'{mk["label"]} ({Decimal(mk["pct"]).normalize():f}%)')
    for code in sorted(seen, key=lambda c: (MARKUP_ORDER.index(c) if c in MARKUP_ORDER else 99, c)):
        markups.append((code, seen[code], totals.get(code, Decimal(0))))
    return {
        "project": {"code": p.code, "name": p.name, "currency": p.currency},
        "estimate": {"code": e.code, "name": e.name},
        "version": {"no": v.version_no, "status": v.status, "pricing_date": v.pricing_date,
                    "approved_by": v.approved_by, "approved_at": v.approved_at},
        "generated_at": datetime.now(timezone.utc),
        "totals": totals,
        "markups": markups,
        "boq": est.boq(s, version_id),
        "items": items,
        "materials": est.resource_summary(s, version_id, "material")
        + est.resource_summary(s, version_id, "subcontract") + est.resource_summary(s, version_id, "other"),
        "labor": est.resource_summary(s, version_id, "labor"),
        "equipment": est.resource_summary(s, version_id, "equipment"),
        "by_trade": est.consolidate(s, version_id, ("trade",)),
        "by_floor": est.consolidate(s, version_id, ("building", "floor")),
        "by_module": est.consolidate(s, version_id, ("module",)),
        "cost_per_area": est.cost_per_area(s, version_id),
    }


def record_report(s: Session, version_id: uuid.UUID, kind: str, data: dict, actor: str) -> None:
    v = s.get(m.EstimateVersion, version_id)
    s.add(m.Report(project_id=v.project_id, estimate_version_id=v.id, kind=kind,
                   payload={"generated_by": actor, "status": v.status,
                            "total": str(money(data["totals"].get("total", Decimal(0))))}))
    s.flush()


def _status_line(d: dict) -> str:
    v = d["version"]
    if v["status"] == "draft":
        return f'DRAFT version {v["no"]}, not approved. Figures may still change.'
    when = v["approved_at"].strftime("%Y-%m-%d") if v["approved_at"] else ""
    return f'Version {v["no"]}, {v["status"]} (approved by {v["approved_by"]} {when})'


# ---------------------------------------------------------------- Excel


def to_excel(d: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F3B64")
    head_font = Font(bold=True, color="FFFFFF")
    bold = Font(bold=True)
    thin = Side(style="thin", color="BFC5CE")
    MONEY, QTY = "#,##0.00", "#,##0.000"
    cur = d["project"]["currency"]

    def f(v):  # Decimal → float for Excel cells, rounded at presentation
        return float(v) if v is not None else None

    def sheet(title: str, headers: list[str], rows: list[list], formats: dict[int, str], widths: list[int],
              total_cols: tuple[int, ...] = ()):
        ws = wb.create_sheet(title)
        ws.append([f'{d["project"]["code"]} · {d["project"]["name"]} · {d["estimate"]["name"]}'])
        ws.append([_status_line(d)])
        ws["A1"].font = Font(bold=True, size=12)
        ws.append([])
        ws.append(headers)
        hdr = ws.max_row
        for c in ws[hdr]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(vertical="center", wrap_text=True)
        for r in rows:
            ws.append(r)
        last = ws.max_row
        for col, fmt in formats.items():
            for row in ws.iter_rows(min_row=hdr + 1, max_row=last, min_col=col, max_col=col):
                row[0].number_format = fmt
        if total_cols and rows:
            ws.append([])
            tr = ws.max_row
            ws.cell(tr, 1, "Total").font = bold
            for col in total_cols:
                letter = get_column_letter(col)
                c = ws.cell(tr, col, f"=SUM({letter}{hdr + 1}:{letter}{last})")
                c.number_format, c.font, c.border = formats.get(col, MONEY), bold, Border(top=thin)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = ws.cell(hdr + 1, 1)
        if rows:
            ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(headers))}{last}"
        return ws

    # Summary
    ws = wb.active
    ws.title = "Summary"
    t = d["totals"]
    ws.append(["CEP: ENTERPRISE — Detailed Estimate"])
    ws["A1"].font = Font(bold=True, size=14)
    for label, value in (("Project", f'{d["project"]["code"]} · {d["project"]["name"]}'),
                         ("Estimate", f'{d["estimate"]["code"]} · {d["estimate"]["name"]}'),
                         ("Status", _status_line(d)),
                         ("Pricing date", d["version"]["pricing_date"].isoformat()),
                         ("Currency", cur),
                         ("Generated", d["generated_at"].strftime("%Y-%m-%d %H:%M UTC"))):
        ws.append([label, value])
    ws.append([])
    ws.append(["Cost element", f"Amount ({cur})"])
    for c in ws[ws.max_row]:
        c.fill, c.font = head_fill, head_font
    lines = [("Materials", t.get("material")), ("Labor", t.get("labor")), ("Equipment", t.get("equipment")),
             ("Subcontract", t.get("subcontract")), ("Other direct costs / provisional sums", t.get("other")),
             ("Direct cost", t.get("direct"))]
    lines += [(label, amount) for _, label, amount in d["markups"]]
    lines += [("TOTAL ESTIMATE", t.get("total"))]
    for label, amount in lines:
        ws.append([label, f(money(amount or Decimal(0)))])
        ws.cell(ws.max_row, 2).number_format = MONEY
        if label in ("Direct cost", "TOTAL ESTIMATE"):
            ws.cell(ws.max_row, 1).font = bold
            ws.cell(ws.max_row, 2).font = bold
    cpa = d["cost_per_area"]
    if cpa["cost_per_m2"] is not None:
        ws.append([])
        ws.append(["Gross floor area (m²)", f(cpa["gross_floor_area_m2"])])
        ws.cell(ws.max_row, 2).number_format = QTY
        ws.append([f"Cost per m² ({cur})", f(money(cpa["cost_per_m2"]))])
        ws.cell(ws.max_row, 2).number_format = MONEY
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 60

    sheet("BOQ", ["No.", "Trade", "Work item", "Code", "Unit", "Quantity", "Unit rate", "Amount"],
          [[b["line_no"], b["trade"], b["description"], b["assembly_code"], b["unit"], f(b["quantity"]),
            f(money(b["unit_rate"])), f(money(b["amount"]))] for b in d["boq"]],
          {6: QTY, 7: MONEY, 8: MONEY}, [6, 16, 48, 18, 8, 14, 14, 18], total_cols=(8,))

    item_rows = []
    for it in d["items"]:
        tt = it.breakdown["totals"]
        item_rows.append([it.item_key, it.module, it.trade, it.description, it.unit, f(Decimal(it.breakdown["quantity"])),
                          f(money(Decimal(tt["material"]))), f(money(Decimal(tt["labor"]))), f(money(Decimal(tt["equipment"]))),
                          f(money(Decimal(tt["subcontract"]) + Decimal(tt["other"]))), f(money(Decimal(tt["direct"]))),
                          f(money(Decimal(tt["total"]))), f(money(Decimal(tt["unit_rate"])))])
    sheet("Items", ["Item", "Module", "Trade", "Description", "Unit", "Quantity", "Material", "Labor", "Equipment",
                    "Other", "Direct", "Amount", "Unit rate"],
          item_rows, {6: QTY, **{c: MONEY for c in range(7, 14)}}, [18, 20, 14, 36, 7, 12, 14, 14, 14, 14, 14, 16, 12],
          total_cols=(7, 8, 9, 10, 11, 12))

    sheet("Materials", ["Code", "Unit", "Quantity", "Amount"],
          [[r["code"], r["unit"], f(r["quantity"]), f(money(r["amount"]))] for r in d["materials"]],
          {3: QTY, 4: MONEY}, [24, 8, 16, 18], total_cols=(4,))
    sheet("Labor", ["Labor class", "Unit", "Hours", "Amount"],
          [[r["code"], r["unit"], f(r["quantity"]), f(money(r["amount"]))] for r in d["labor"]],
          {3: QTY, 4: MONEY}, [24, 8, 16, 18], total_cols=(3, 4))
    sheet("Equipment", ["Equipment", "Unit", "Hours", "Amount"],
          [[r["code"], r["unit"], f(r["quantity"]), f(money(r["amount"]))] for r in d["equipment"]],
          {3: QTY, 4: MONEY}, [24, 8, 16, 18], total_cols=(3, 4))
    total = d["totals"].get("total") or Decimal(0)
    sheet("By trade", ["Trade", "Items", "Direct", "Amount", "Share"],
          [[r["trade"], r["items"], f(money(r["direct"])), f(money(r["amount"])),
            f(r["amount"] / total) if total else 0] for r in d["by_trade"]],
          {3: MONEY, 4: MONEY, 5: "0.0%"}, [24, 8, 18, 18, 10], total_cols=(3, 4))
    sheet("By floor", ["Building", "Floor", "Items", "Direct", "Amount"],
          [[r["building"] or "(none)", r["floor"] or "(none)", r["items"], f(money(r["direct"])), f(money(r["amount"]))]
           for r in d["by_floor"]],
          {4: MONEY, 5: MONEY}, [14, 12, 8, 18, 18], total_cols=(4, 5))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF


def to_pdf(d: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cur = d["project"]["currency"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f'{d["project"]["code"]} {d["estimate"]["name"]}', author="CEP: ENTERPRISE")

    def amt(v) -> str:
        return f"{money(v or Decimal(0)):,.2f}"

    def q(v) -> str:
        return f"{v.quantize(Decimal('0.001')):,.3f}".rstrip("0").rstrip(".")

    def table(rows, widths, num_cols=(), total_row=False):
        tbl = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B64")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFC5CE")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for c in num_cols:
            style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        if total_row:
            style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]
        tbl.setStyle(TableStyle(style))
        return tbl

    cell = styles["BodyText"].clone("cell", fontSize=8, leading=10)
    story = [
        Paragraph("CEP: ENTERPRISE — Detailed Estimate", styles["Title"]),
        Paragraph(f'<b>{d["project"]["code"]}</b> · {d["project"]["name"]} · {d["estimate"]["code"]} {d["estimate"]["name"]}',
                  styles["Normal"]),
        Paragraph(f'{_status_line(d)} · Pricing date {d["version"]["pricing_date"].isoformat()} · Currency {cur}',
                  styles["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("Executive summary", styles["Heading2"]),
    ]
    t = d["totals"]
    rows = [["Cost element", f"Amount ({cur})"],
            ["Materials", amt(t.get("material"))], ["Labor", amt(t.get("labor"))], ["Equipment", amt(t.get("equipment"))],
            ["Subcontract", amt(t.get("subcontract"))], ["Other direct costs / provisional sums", amt(t.get("other"))],
            ["Direct cost", amt(t.get("direct"))]]
    rows += [[label, amt(a)] for _, label, a in d["markups"]]
    rows.append(["TOTAL ESTIMATE", amt(t.get("total"))])
    story.append(table(rows, [110 * mm, 50 * mm], num_cols=(1,), total_row=True))
    cpa = d["cost_per_area"]
    if cpa["cost_per_m2"] is not None:
        story += [Spacer(1, 2 * mm),
                  Paragraph(f'Gross floor area {q(cpa["gross_floor_area_m2"])} m² · cost per m² {cur} {amt(cpa["cost_per_m2"])}',
                            styles["Normal"])]

    total = t.get("total") or Decimal(0)
    story += [Spacer(1, 5 * mm), CondPageBreak(40 * mm), Paragraph("Trade cost analysis", styles["Heading2"])]
    rows = [["Trade", "Items", f"Amount ({cur})", "Share"]]
    rows += [[r["trade"], str(r["items"]), amt(r["amount"]), f'{(r["amount"] / total * 100) if total else 0:.1f}%'] for r in d["by_trade"]]
    story.append(table(rows, [80 * mm, 20 * mm, 45 * mm, 25 * mm], num_cols=(1, 2, 3)))

    story += [Spacer(1, 5 * mm), CondPageBreak(50 * mm), Paragraph("Bill of quantities", styles["Heading2"])]
    rows = [["No.", "Trade", "Work item", "Unit", "Quantity", "Unit rate", "Amount"]]
    for b in d["boq"]:
        rows.append([str(b["line_no"]), b["trade"], Paragraph(f'{b["description"]} <font color="#666666">{b["assembly_code"]}</font>', cell),
                     b["unit"], q(b["quantity"]), amt(b["unit_rate"]), amt(b["amount"])])
    rows.append(["", "", "Total", "", "", "", amt(sum((b["amount"] for b in d["boq"]), Decimal(0)))])
    story.append(table(rows, [12 * mm, 32 * mm, 110 * mm, 14 * mm, 28 * mm, 28 * mm, 35 * mm], num_cols=(4, 5, 6), total_row=True))

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(14 * mm, 8 * mm, f'{d["project"]["code"]} · {_status_line(d)}')
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
