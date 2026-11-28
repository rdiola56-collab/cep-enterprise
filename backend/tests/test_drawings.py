"""Drawings read directly: DWG (LibreDWG), Revit (Autodesk's Revit-to-IFC export) and PDF measured on screen."""
import base64
import json
from decimal import Decimal as Dec

import pytest

from app.integrations import ADAPTERS, SourceError, adapter_info, dwg, rvt
from app.integrations import pdf_takeoff as pt
from app.integrations.base import Measure

from . import import_samples as smp
from .test_api import auth, client  # noqa: F401  (fixture)
from .test_integrations import WALL, _plan


def _plan_more(doc, ms):
    _plan(doc, ms)
    ms.add_circle((100, 100), 250, dxfattribs={"layer": "A-COL"})
    ms.add_ellipse((0, 0), (500, 0, 0), 0.5, dxfattribs={"layer": "A-ELL"})
    ms.add_lwpolyline([(0, 0, 0.5), (1000, 0, 0), (1000, 1000, -0.3), (0, 1000, 0)], format="xyb", close=True,
                      dxfattribs={"layer": "A-BULGE"})
    blk = doc.blocks.new("WIN")
    blk.add_line((0, 0), (1200, 0))
    blk.add_attdef("TYPE", (0, 0))
    ms.add_blockref("WIN", (10, 10), dxfattribs={"layer": "A-GLAZ"}).add_attrib("TYPE", "W1", (0, 0))


def _rows(src):
    return sorted((r.source_ref, r.group, r.label, r.level, tuple(sorted((k, str(x.value), x.unit) for k, x in r.measures.items())),
                   tuple(sorted(r.properties.items()))) for r in src.rows)


# --------------------------------------------------------------------------- DWG


@pytest.mark.parametrize("version", ["AC1015", "AC1018", "AC1024", "AC1027", "AC1032"])
def test_dwg_gives_exactly_what_the_same_drawing_gives_as_dxf(version):
    drawing = smp.dxf(4, _plan_more)
    as_dxf = ADAPTERS["dxf"](drawing, {"level": "L02"})
    as_dwg = ADAPTERS["dwg"](smp.dwg(drawing, version), {"level": "L02"})
    assert as_dwg.adapter == "dwg" and len(as_dwg.rows) == 9
    assert _rows(as_dwg) == _rows(as_dxf)  # same entity handles, values, units, block attributes
    assert as_dwg.warnings == as_dxf.warnings and as_dwg.info["unit"] == "mm"
    assert as_dwg.info["dwg_version"] == version and as_dwg.info["read_by"].startswith("LibreDWG")


def test_dwg_refuses_what_it_cannot_read(monkeypatch):
    with pytest.raises(SourceError, match="not a DWG"):
        ADAPTERS["dwg"](b"%PDF-1.7 not a drawing")
    with pytest.raises(SourceError, match="before AutoCAD R13"):
        ADAPTERS["dwg"](b"AC1009" + bytes(100))
    with pytest.raises(SourceError, match="could not read"):
        ADAPTERS["dwg"](b"AC1018" + bytes(200))  # right signature, corrupt body
    with pytest.raises(SourceError, match="no units"):  # the DXF rule applies to DWG too: no guessing
        ADAPTERS["dwg"](smp.dwg(smp.dxf(0, _plan)))
    assert adapter_info()["dwg"]["status"] == "operational"
    monkeypatch.setattr(dwg, "command", lambda: None)
    assert adapter_info()["dwg"]["status"].startswith("not available")
    with pytest.raises(SourceError, match="not installed"):
        ADAPTERS["dwg"](smp.dwg(smp.dxf(4, _plan)))


# --------------------------------------------------------------------------- Revit through APS


class FakeAPS:
    """Autodesk Platform Services as far as CEP uses it: token, OSS signed upload, Model Derivative IFC export."""

    def __init__(self, ifc_bytes: bytes, polls_before_done: int = 2, fail: bool = False):
        self.ifc, self.left, self.fail = ifc_bytes, polls_before_done, fail
        self.calls: list[tuple[str, str]] = []
        self.uploaded = b""
        self.job = None

    def __call__(self, method, url, *, headers=None, json=None, data=None, content=None):
        headers = headers or {}
        self.calls.append((method, url.split("?")[0]))
        ok = lambda body: (200, {"set-cookie": []}, __import__("json").dumps(body).encode())  # noqa: E731
        if url.endswith("/authentication/v2/token"):
            assert headers["Authorization"] == "Basic " + base64.b64encode(b"cid:secret").decode()
            assert data["grant_type"] == "client_credentials" and "bucket:create" in data["scope"]
            return ok({"access_token": "tok", "expires_in": 3599})
        if url.startswith("https://developer.api.autodesk.com/"):
            assert headers.get("Authorization") == "Bearer tok", url
        else:  # signed S3 and CDN URLs: the bearer token must never be sent there
            assert "Authorization" not in headers, url
        if url.endswith("/oss/v2/buckets"):
            assert json["policyKey"] == "transient" and headers["x-ads-region"] == "US"
            return 409, {}, b'{"reason":"Bucket already exists"}'
        if "/signeds3upload" in url and method == "GET":
            return ok({"uploadKey": "uk", "urls": ["https://s3.example/put"]})
        if url == "https://s3.example/put":
            self.uploaded = content
            return 200, {}, b""
        if "/signeds3upload" in url and method == "POST":
            assert json == {"uploadKey": "uk"}
            key = url.split("/objects/")[1].split("/")[0]
            return ok({"objectId": f"urn:adsk.objects:os.object:cep-bucket/{key}"})
        if url.endswith("/designdata/job"):
            assert headers["x-ads-force"] == "true"
            self.job = json
            return ok({"result": "created"})
        if url.endswith("/manifest"):
            if self.fail:
                return ok({"status": "failed", "derivatives": [{"outputType": "ifc", "messages": [{"message": "Revit could not open the file"}]}]})
            if self.left > 0:
                self.left -= 1
                return ok({"status": "inprogress", "progress": "40% complete"})
            urn = self.job["input"]["urn"]
            return ok({"status": "success", "derivatives": [{"outputType": "ifc", "status": "success", "children": [
                {"urn": f"urn:adsk.viewing:fs.file:{urn}/output/Tower.ifc", "role": "ifc", "type": "resource"}]}]})
        if url.endswith("/signedcookies"):
            return 200, {"set-cookie": ["CloudFront-Policy=p; Path=/", "CloudFront-Signature=s; Path=/"]}, \
                b'{"url": "https://cdn.example/Tower.ifc", "size": 1}'
        if url == "https://cdn.example/Tower.ifc":
            assert headers["Cookie"] == "CloudFront-Policy=p; CloudFront-Signature=s"
            return 200, {}, self.ifc
        raise AssertionError(f"unexpected call {method} {url}")


RVT = bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 512  # an OLE container; only APS opens it


@pytest.fixture
def aps(monkeypatch):
    monkeypatch.setenv("CEP_APS_CLIENT_ID", "cid")
    monkeypatch.setenv("CEP_APS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CEP_APS_IFC_SETTING", "IFC2x3 Coordination View 2.0")
    fake = FakeAPS(smp.ifc([WALL]))
    monkeypatch.setattr(rvt, "http", fake)
    monkeypatch.setattr(rvt, "sleep", lambda s: None)
    return fake


def test_revit_is_measured_as_autodesks_own_ifc_export(aps):
    src = ADAPTERS["rvt"](RVT, {})
    direct = ADAPTERS["ifc"](aps.ifc, {})  # the very IFC file APS handed back
    assert src.adapter == "rvt" and _rows(src) == _rows(direct)
    assert aps.uploaded == RVT
    assert aps.job["output"]["formats"] == [{"type": "ifc", "advanced": {"exportSettingName": "IFC2x3 Coordination View 2.0"}}]
    object_id = base64.urlsafe_b64decode(aps.job["input"]["urn"] + "==").decode()
    assert object_id.startswith("urn:adsk.objects:os.object:cep-bucket/") and object_id.endswith(".rvt")
    assert sum(1 for c in aps.calls if c[1].endswith("/manifest")) == 3  # waited for the export
    assert src.info["read_by"].startswith("Autodesk Platform Services") and src.info["aps_urn"] == aps.job["input"]["urn"]


def test_revit_is_off_until_configured_and_reports_failures(monkeypatch, aps):
    with pytest.raises(SourceError, match="not a Revit model"):
        ADAPTERS["rvt"](b"PK\x03\x04 a zip, not an rvt")
    aps.fail = True
    with pytest.raises(SourceError, match="Revit could not open the file"):
        ADAPTERS["rvt"](RVT)
    aps.fail, aps.left = False, 10**6
    monkeypatch.setenv("CEP_APS_TIMEOUT", "20")
    with pytest.raises(SourceError, match="not finished after 20 s"):
        ADAPTERS["rvt"](RVT)
    monkeypatch.delenv("CEP_APS_CLIENT_ID")
    assert adapter_info()["rvt"]["status"].startswith("needs setup")
    with pytest.raises(SourceError, match="not set up"):
        ADAPTERS["rvt"](RVT)


# --------------------------------------------------------------------------- PDF scale arithmetic


def test_pdf_scales_are_exact():
    unit, k = pt.scale_from_stated(Measure(Dec("0.25"), "in"), Measure(Dec(1), "ft"))  # 1/4" = 1'-0"
    assert unit == "ft" and k * 72 == 4  # one inch of paper is four feet
    ms, problems = pt.measures("length", [["0", "0"], ["72", "0"], ["72", "36"]], unit, format(k, "f"))
    assert not problems and ms["length"].value == 6 and ms["length"].unit == "ft"
    unit, k = pt.scale_from_stated(Measure(Dec(1), "mm"), Measure(Dec(100), "mm"))  # 1:100
    assert unit == "mm"
    ms, _ = pt.measures("area", [["0", "0"], ["72", "0"], ["72", "72"], ["0", "72"]], unit, format(k, "f"))
    assert ms["area"].value == Dec("2540") ** 2 and ms["area"].unit == "mm2" and ms["perimeter"].value == 4 * Dec(2540)
    unit, k = pt.scale_from_measured(["10", "10"], ["154", "10"], Measure(Dec(20), "ft"))  # a known 20 ft dimension
    assert pt.measures("length", [["0", "0"], ["144", "0"]], unit, format(k, "f"))[0]["length"].value == 20
    assert pt.measures("count", [["1", "1"], ["2", "2"]], None, None)[0]["count"].value == 2  # counts need no scale
    assert pt.measures("length", [["0", "0"], ["1", "1"]], None, None)[1]  # lengths do
    with pytest.raises(ValueError, match="positive length"):
        pt.scale_from_stated(Measure(Dec(1), "kg"), Measure(Dec(1), "ft"))
    with pytest.raises(ValueError, match="further apart"):
        pt.scale_from_measured(["1", "1"], ["1.1", "1"], Measure(Dec(1), "ft"))


# --------------------------------------------------------------------------- PDF takeoff over HTTP


def test_pdf_takeoff_from_upload_to_estimate(client):  # noqa: F811
    h, view = auth(client, "est@cep.test"), auth(client, "view@cep.test")
    pid = client.post("/api/projects", json={"code": "PDF", "name": "PDF takeoff", "currency": "PHP"}, headers=h).json()["id"]
    viewer = client.get("/api/auth/me", headers=view).json()["id"]
    assert client.put(f"/api/projects/{pid}/members", json={"user_id": viewer, "role": "viewer"}, headers=h).status_code == 200
    b = client.post(f"/api/projects/{pid}/buildings", json={"code": "T1", "name": "Tower"}, headers=h).json()["id"]
    client.post(f"/api/projects/{pid}/floors", headers=h, json={"building_id": b, "code": "L02", "name": "Level 2", "level": 2})
    v = client.post(f"/api/projects/{pid}/estimates", headers=h,
                    json={"code": "E", "name": "E", "pricing_date": "2026-11-01"}).json()["version_id"]
    rule = {"code": "TILE", "adapter": "pdf_takeoff", "match_group": "Floor Tile", "module": "tiling",
            "item_type": "floor_tile", "input_map": {"measured": "area"}}
    assert client.post(f"/api/projects/{pid}/import-rules", json=rule, headers=h).status_code == 201
    assert client.post(f"/api/projects/{pid}/import-rules", json={**rule, "code": "X", "adapter": "dwg"},
                       headers=h).json()["detail"].startswith("dwg files use the dxf rules")

    pdf = smp.pdf_plan([{"lines": [((100, 100), (388, 100)), ((388, 100), (388, 316))]},
                        {"size": (612, 792), "rotate": 90, "lines": [((50, 60), (122, 60))]}])
    url = f"/api/projects/{pid}/sheets"
    files = {"file": ("A-102.pdf", pdf, "application/pdf")}
    assert client.post(url, data={"set_name": "A-100 series", "revision": "B"}, files=files, headers=view).status_code in (403, 404)
    assert client.post(url, data={"set_name": "A", "revision": "B"}, files={"file": ("x.pdf", b"nope", "application/pdf")},
                       headers=h).status_code == 422
    sheet = client.post(url, data={"set_name": "A-100 series", "revision": "B"}, files=files, headers=h).json()
    surl = f"{url}/{sheet['id']}"
    assert sheet["page_count"] == 2 and sheet["pages"][0]["width_pt"] == "1224.000" and sheet["pages"][1]["rotation"] == 90
    assert sheet["pages"][1]["width_pt"] == "792.000"  # sizes as displayed
    img = client.get(f"{surl}/pages/1/image", params={"scale": 1}, headers=view)
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    snaps = client.get(f"{surl}/pages/1/snaps", headers=view).json()["points"]
    assert [100.0, 100.0] in snaps and [388.0, 316.0] in snaps
    assert [732.0, 50.0] in client.get(f"{surl}/pages/2/snaps", headers=view).json()["points"]  # rotated page, displayed frame

    # Measuring before the page has a scale shows no quantity, and cannot be imported.
    tile = {"page_no": 1, "kind": "area", "subject": "Floor Tile", "label": "Lobby",
            "points": [[100, 100], [388, 100], [388, 316], [100, 316]]}
    m1 = client.post(f"{surl}/marks", json=tile, headers=h).json()
    assert m1["ref"] == "M1" and m1["measures"] == {} and "no scale" in m1["problems"][0]
    assert client.post(f"{surl}/marks", json={**tile, "points": [[0, 0], [5000, 0], [0, 5]]}, headers=h).status_code == 422
    assert client.post(f"{surl}/marks", json={**tile, "subject": " "}, headers=h).status_code == 422
    assert client.post(f"{surl}/marks", json={**tile, "kind": "volume"}, headers=h).status_code == 422

    assert client.put(f"{surl}/pages/1/scale", json={"method": "stated", "paper": "1/4\"", "real": "1'-0\""},
                      headers=h).status_code == 200
    detail = client.get(surl, headers=h).json()
    assert detail["pages"][0]["scale"]["unit"] == "ft" and detail["pages"][0]["scale"]["note"] == "1/4\" = 1'-0\""
    # 288 × 216 points at 1/4" = 1'-0" is 16 ft × 12 ft
    assert detail["marks"][0]["measures"]["area"] == {"value": "192", "unit": "ft2"}
    assert detail["marks"][0]["measures"]["perimeter"] == {"value": "56", "unit": "ft"}
    client.patch(f"{surl}/pages/1", json={"label": "L02"}, headers=h)
    m2 = client.post(f"{surl}/marks", headers=h, json={"page_no": 1, "kind": "length", "subject": "Skirting",
                                                       "points": [[100, 100], [388, 100], [388, 316]]}).json()
    assert m2["ref"] == "M2" and m2["measures"]["length"] == {"value": "28", "unit": "ft"}
    m3 = client.post(f"{surl}/marks", headers=h, json={"page_no": 2, "kind": "count", "subject": "Floor drain",
                                                       "points": [[10, 10], [20, 20], [30, 30]]}).json()
    assert m3["measures"]["count"] == {"value": "3", "unit": "pcs"}

    # A known dimension also sets a scale: 288 points measured as 16 ft gives the same 1/4" scale.
    client.put(f"{surl}/pages/1/scale", json={"method": "measured", "p1": [100, 100], "p2": [388, 100], "length": "16 ft"}, headers=h)
    assert client.get(surl, headers=h).json()["marks"][0]["measures"]["area"]["value"] == "192"
    assert client.put(f"{surl}/pages/1/scale", json={"method": "stated", "paper": "1 in", "real": "3 kg"}, headers=h).status_code == 422

    # Send to the estimate: the ordinary import review, rules and commit.
    batch = client.post(f"{surl}/send", json={"version_id": v, "building_id": b}, headers=h).json()
    assert batch["adapter"] == "pdf_takeoff" and batch["summary"]["counts"] == {"mapped": 1, "unmapped": 2}
    tile_row = next(r for r in batch["rows"] if r["source_ref"] == "M1")
    assert tile_row["measures"]["area"] == {"value": "192", "unit": "ft2"} and tile_row["level"] == "L02"
    burl = f"/api/projects/{pid}/imports/{batch['id']}"
    others = [r["id"] for r in batch["rows"] if r["status"] == "unmapped"]
    client.post(f"{burl}/exclude", json={"row_ids": others, "reason": "priced elsewhere"}, headers=h)
    assert client.post(f"{burl}/commit", json={}, headers=h).json()["status"] == "committed"
    items = client.get(f"/api/projects/{pid}/versions/{v}/items", headers=h).json()
    assert [(i["item_key"], i["quantity_basis"]) for i in items] == [("pdf_takeoff:A-100-series:M1", "measured")]
    assert items[0]["floor_id"]  # page label L02 matched the WBS floor

    # Editing the mark and re-sending updates the same item.
    client.patch(f"{surl}/marks/{m1['id']}", json={"points": [[100, 100], [388, 100], [388, 388], [100, 388]]}, headers=h)
    again = client.post(f"{surl}/send", json={"version_id": v}, headers=h).json()
    assert next(r for r in again["rows"] if r["source_ref"] == "M1")["measures"]["area"]["value"] == "256"
    assert client.delete(f"{surl}/marks/{m3['id']}", headers=view).status_code in (403, 404)
    assert client.delete(f"{surl}/marks/{m3['id']}", headers=h).status_code == 204
    assert [x["ref"] for x in client.get(surl, headers=h).json()["marks"]] == ["M1", "M2"]
    assert client.get(f"/api/projects/{pid}/sheets", headers=view).json()[0]["marks"] == 2
    assert client.post(f"/api/projects/{pid}/versions/{v}/imports", headers=h, data={"adapter": "pdf_takeoff", "source_name": "x",
                       "source_revision": "1"}, files=files).status_code == 422  # measured on screen, not uploaded


def test_dwg_and_revit_share_dxf_and_ifc_rules_over_http(client, aps):  # noqa: F811
    h = auth(client, "est@cep.test")
    pid = client.post("/api/projects", json={"code": "DW", "name": "DWG", "currency": "PHP"}, headers=h).json()["id"]
    v = client.post(f"/api/projects/{pid}/estimates", headers=h,
                    json={"code": "E", "name": "E", "pricing_date": "2026-11-01"}).json()["version_id"]
    client.post(f"/api/projects/{pid}/import-rules", headers=h, json={
        "code": "TILE", "adapter": "dxf", "match_group": "A-FLOR-TILE", "module": "tiling", "item_type": "floor_tile",
        "input_map": {"measured": "area"}})
    url = f"/api/projects/{pid}/versions/{v}/imports"
    form = {"adapter": "dwg", "source_name": "L02 plan", "source_revision": "1"}
    drawing = smp.dxf(4, _plan)
    batch = client.post(url, data=form, files={"file": ("L02.dwg", smp.dwg(drawing), "application/acad")}, headers=h).json()
    assert batch["summary"]["info"]["autocad_release"] == "2004"
    tile = next(r for r in batch["rows"] if r["status"] == "mapped")
    assert tile["item_key"].startswith("dxf:L02-plan:")  # same key a DXF of this drawing gets
    rest = [r["id"] for r in batch["rows"] if r["status"] != "mapped"]
    client.post(f"/api/projects/{pid}/imports/{batch['id']}/exclude", json={"row_ids": rest, "reason": "not in scope"}, headers=h)
    client.post(f"/api/projects/{pid}/imports/{batch['id']}/commit", json={}, headers=h)
    # The same drawing later arrives as DXF: it updates the item instead of adding a second one.
    again = client.post(url, data={**form, "adapter": "dxf", "source_revision": "2"},
                        files={"file": ("L02.dxf", drawing, "application/dxf")}, headers=h).json()
    assert next(r for r in again["rows"] if r["status"] == "mapped")["item_key"] == tile["item_key"]

    # Revit always runs as a background job (translation takes minutes) and uses the IFC rules.
    client.post(f"/api/projects/{pid}/import-rules", headers=h, json={
        "code": "WALL", "adapter": "ifc", "match_group": "IfcWall*", "module": "walls_partitions", "item_type": "chb_wall",
        "quantity_basis": "measured", "input_map": {"measured": "NetSideArea"}})
    r = client.post(url, data={**form, "adapter": "rvt", "source_name": "Tower model"},
                    files={"file": ("Tower.rvt", RVT, "application/octet-stream")}, headers=h)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "succeeded" and job["result"]["counts"] == {"mapped": 1}
    rb = client.get(f"/api/projects/{pid}/imports/{job['result']['batch_id']}", headers=h).json()
    assert rb["adapter"] == "rvt" and rb["rows"][0]["item_key"].startswith("ifc:Tower-model:")
    assert json.dumps(rb["summary"]["info"]).count("Autodesk Platform Services") == 1
