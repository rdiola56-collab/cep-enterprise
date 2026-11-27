"""End-to-end through HTTP: the API is a thin layer over the same engine."""
import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app

from .db_seed import seed_master


@pytest.fixture
def client(db):
    seed_master(db)

    def _session():
        yield db

    app.dependency_overrides[get_session] = _session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_units_and_modules(client):
    r = client.post("/api/units/convert", json={"value": "10", "from_unit": "ft", "to_unit": "m"})
    assert r.json()["value"] == "3.048"
    assert client.post("/api/units/convert", json={"value": "1", "from_unit": "m", "to_unit": "kg"}).status_code == 422
    mods = client.get("/api/modules").json()
    assert len(mods) == 19 and sum(m["status"] == "configured" for m in mods) == 5


def test_formula_publish_version_evaluate(client):
    body = {"code": "PLASTER_AREA", "name": "Plaster both faces", "expression": "2 × length × height − openings",
            "output_kind": "area", "output_unit": "m2",
            "inputs": [{"name": "length", "kind": "length", "unit": "m"}, {"name": "height", "kind": "length", "unit": "m"},
                       {"name": "openings", "kind": "area", "unit": "m2", "default": "0"}]}
    assert client.post("/api/formulas", json=body).json()["version"] == 1
    assert client.post("/api/formulas", json=body).status_code == 422  # unchanged
    assert client.post("/api/formulas", json={**body, "expression": "length + height"}).status_code == 422  # dimension error
    assert client.post("/api/formulas", json={**body, "expression": "__import__('os')"}).status_code == 422
    r = client.post("/api/formulas/PLASTER_AREA/evaluate", json={"inputs": {"length": {"value": "10", "unit": "m"},
                                                                            "height": {"value": "3", "unit": "m"}}})
    assert r.status_code == 200 and r.json()["value"] == "60"


def test_estimate_flow_and_project_isolation(client):
    a = client.post("/api/projects", json={"code": "A", "name": "Tower A", "currency": "PHP"}).json()["id"]
    b = client.post("/api/projects", json={"code": "B", "name": "Tower B", "currency": "PHP"}).json()["id"]
    v = client.post(f"/api/projects/{a}/estimates", json={"code": "E1", "name": "Main", "pricing_date": "2026-11-01"}).json()
    item = {"item_key": "tile-L1", "module": "tiling", "item_type": "floor_tile",
            "inputs": {"length": {"value": "20", "unit": "m"}, "width": {"value": "5", "unit": "m"}}}
    url = f"/api/projects/{a}/versions/{v['version_id']}"
    r = client.post(f"{url}/items", json=item)
    assert r.status_code == 200, r.text
    assert r.json()["amount"].startswith("62584.433")
    assert client.get(f"/api/projects/{b}/versions/{v['version_id']}/items").status_code == 404  # wrong project
    assert client.post(f"{url}/items", json={**item, "module": "painting"}).status_code == 422  # not configured
    boq = client.get(f"{url}/boq").json()
    assert boq[0]["quantity"].startswith("100")
    assert client.post(f"{url}/approve", headers={"X-CEP-User": "pm"}).json()["status"] == "approved"
    assert client.post(f"{url}/items", json=item).status_code == 409
    v2 = client.post(f"{url}/revisions", json={}).json()["version_id"]
    diff = client.get(f"/api/projects/{a}/compare", params={"old": v["version_id"], "new": v2}).json()
    assert diff["changes"] == []
