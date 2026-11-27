"""Excel and PDF exports carry the same figures as the API."""
import io
from decimal import Decimal as Dec

from openpyxl import load_workbook

from .test_api import TILE, auth, client  # noqa: F401  (fixture)

BEAM = {"item_key": "beam", "module": "reinforced_concrete", "item_type": "beam",
        "inputs": {"length": {"value": "6", "unit": "m"}, "width": {"value": "300", "unit": "mm"},
                   "height": {"value": "600", "unit": "mm"}, "pieces": {"value": "10", "unit": "pcs"}}}


def _estimate(client):  # noqa: F811
    h = auth(client, "est@cep.test")
    pid = client.post("/api/projects", json={"code": "RPT", "name": "Report Tower", "currency": "PHP"}, headers=h).json()["id"]
    v = client.post(f"/api/projects/{pid}/estimates", headers=h,
                    json={"code": "E1", "name": "Tender", "pricing_date": "2026-11-01"}).json()["version_id"]
    url = f"/api/projects/{pid}/versions/{v}"
    assert client.post(f"{url}/items", json=TILE, headers=h).status_code == 200
    assert client.post(f"{url}/items", json=BEAM, headers=h).status_code == 200
    return h, url


def test_excel_export_matches_api(client):  # noqa: F811
    h, url = _estimate(client)
    r = client.get(f"{url}/export.xlsx", headers=h)
    assert r.status_code == 200
    assert 'filename="RPT_estimate_v1_draft.xlsx"' in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Summary", "BOQ", "Items", "Materials", "Labor", "Equipment", "By trade", "By floor"]
    summary = {row[0]: row[1] for row in wb["Summary"].iter_rows(values_only=True) if row and row[0]}
    assert summary["Status"].startswith("DRAFT")
    api_total = Dec(client.get(f"{url}/summary", headers=h).json()["totals"]["total"])
    assert Dec(str(summary["TOTAL ESTIMATE"])) == api_total.quantize(Dec("0.01"))
    boq = wb["BOQ"]
    rows = [r for r in boq.iter_rows(min_row=5, values_only=True) if r[0]]
    assert rows[-1][0] == "Total" and rows[-1][7].startswith("=SUM(H5:H")
    tile = next(r for r in rows if r[3] == "TILE-FLR600")
    assert tile[5] == 100 and Dec(str(tile[7])) == Dec("62584.43")


def test_pdf_export(client):  # noqa: F811
    h, url = _estimate(client)
    r = client.get(f"{url}/export.pdf", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF-") and len(r.content) > 2000
    assert r.headers["content-type"] == "application/pdf"


def test_exports_need_project_access_and_are_logged(client, db):  # noqa: F811
    from sqlalchemy import select

    from app.db.models import Report

    h, url = _estimate(client)
    assert client.get(f"{url}/export.pdf", headers=auth(client, "outsider@cep.test")).status_code == 404
    client.get(f"{url}/export.xlsx", headers=h)
    kinds = [r.kind for r in db.scalars(select(Report))]
    assert "estimate.xlsx" in kinds
