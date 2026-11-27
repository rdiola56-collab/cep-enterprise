"""End-to-end through HTTP, including sign-in and role permissions."""
import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app
from app.services.users import create_user

from .db_seed import seed_master

PW = "correct-horse-battery"
TILE = {"item_key": "tile-L1", "module": "tiling", "item_type": "floor_tile",
        "inputs": {"length": {"value": "20", "unit": "m"}, "width": {"value": "5", "unit": "m"}}}


@pytest.fixture
def client(db):
    seed_master(db)
    for email, role in (("admin@cep.test", "admin"), ("ce@cep.test", "cost_engineer"), ("est@cep.test", "estimator"),
                        ("approver@cep.test", "viewer"), ("outsider@cep.test", "estimator"), ("view@cep.test", "viewer")):
        create_user(db, email, email.split("@")[0], PW, role)

    def _session():
        yield db

    app.dependency_overrides[get_session] = _session
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_sign_in_is_required(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/modules").status_code == 401
    assert client.get("/api/modules", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@cep.test", "password": "wrong-password"}).status_code == 401
    h = auth(client, "Admin@CEP.test")
    assert client.get("/api/auth/me", headers=h).json()["global_role"] == "admin"


def test_logout_everywhere_revokes_tokens(client):
    h = auth(client, "est@cep.test")
    assert client.post("/api/auth/logout-everywhere", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_units_and_modules(client):
    h = auth(client, "view@cep.test")
    r = client.post("/api/units/convert", json={"value": "10", "from_unit": "ft", "to_unit": "m"}, headers=h)
    assert r.json()["value"] == "3.048"
    mods = client.get("/api/modules", headers=h).json()
    assert len(mods) == 19 and all(m["status"] == "configured" for m in mods)


def test_master_library_needs_a_library_role(client):
    body = {"code": "PLASTER_AREA", "name": "Plaster both faces", "expression": "2 × length × height − openings",
            "output_kind": "area", "output_unit": "m2",
            "inputs": [{"name": "length", "kind": "length", "unit": "m"}, {"name": "height", "kind": "length", "unit": "m"},
                       {"name": "openings", "kind": "area", "unit": "m2", "default": "0"}]}
    assert client.post("/api/formulas", json=body, headers=auth(client, "est@cep.test")).status_code == 403
    ce = auth(client, "ce@cep.test")
    assert client.post("/api/formulas", json=body, headers=ce).json()["version"] == 1
    assert client.post("/api/formulas", json=body, headers=ce).status_code == 422  # unchanged
    assert client.post("/api/formulas", json={**body, "expression": "length + height"}, headers=ce).status_code == 422
    assert client.post("/api/formulas", json={**body, "expression": "__import__('os')"}, headers=ce).status_code == 422
    r = client.post("/api/formulas/PLASTER_AREA/evaluate", headers=ce,
                    json={"inputs": {"length": {"value": "10", "unit": "m"}, "height": {"value": "3", "unit": "m"}}})
    assert r.json()["value"] == "60"
    price = {"material_code": "TILE-600", "unit": "pcs", "price": "1", "currency": "PHP", "valid_from": "2026-01-01"}
    assert client.post("/api/library/prices", json=price, headers=auth(client, "est@cep.test")).status_code == 403


def test_roles_and_project_isolation_over_http(client, db):
    est_h, appr_h, out_h = auth(client, "est@cep.test"), auth(client, "approver@cep.test"), auth(client, "outsider@cep.test")
    pid = client.post("/api/projects", json={"code": "A", "name": "Tower A", "currency": "PHP"}, headers=est_h).json()["id"]
    other = client.post("/api/projects", json={"code": "B", "name": "Tower B", "currency": "PHP"}, headers=out_h).json()["id"]
    v = client.post(f"/api/projects/{pid}/estimates", headers=est_h,
                    json={"code": "E1", "name": "Main", "pricing_date": "2026-11-01"}).json()
    url = f"/api/projects/{pid}/versions/{v['version_id']}"

    r = client.post(f"{url}/items", json=TILE, headers=est_h)
    assert r.status_code == 200, r.text
    assert r.json()["amount"].startswith("62584.433")

    # Outsiders cannot see the project at all, even by guessing ids.
    assert client.get(f"{url}/items", headers=out_h).status_code == 404
    assert client.get(f"/api/projects/{other}/versions/{v['version_id']}/items", headers=out_h).status_code == 404
    assert [p["code"] for p in client.get("/api/projects", headers=out_h).json()] == ["B"]

    # The estimator (project manager by creation) adds an approver; approvers approve but cannot edit.
    me = client.get("/api/auth/me", headers=appr_h).json()["id"]
    assert client.put(f"/api/projects/{pid}/members", json={"user_id": me, "role": "approver"}, headers=out_h).status_code == 404
    assert client.put(f"/api/projects/{pid}/members", json={"user_id": me, "role": "approver"}, headers=est_h).status_code == 200
    assert client.post(f"{url}/items", json=TILE, headers=appr_h).status_code == 403
    assert client.post(f"{url}/approve", headers=appr_h).json()["approved_by"] == "approver@cep.test"
    assert client.post(f"{url}/items", json=TILE, headers=est_h).status_code == 409  # frozen

    v2 = client.post(f"{url}/revisions", json={}, headers=est_h).json()["version_id"]
    diff = client.get(f"/api/projects/{pid}/compare", params={"old": v["version_id"], "new": v2}, headers=appr_h).json()
    assert diff["changes"] == []


def test_wbs_consolidation_and_cost_per_area(client):
    h = auth(client, "est@cep.test")
    pid = client.post("/api/projects", json={"code": "W", "name": "WBS", "currency": "PHP"}, headers=h).json()["id"]
    b = client.post(f"/api/projects/{pid}/buildings", json={"code": "T1", "name": "Tower 1"}, headers=h).json()["id"]
    floors = {}
    for code, level, basement in (("B1", -1, True), ("L01", 1, False), ("L02", 2, False)):
        floors[code] = client.post(f"/api/projects/{pid}/floors", headers=h, json={
            "building_id": b, "code": code, "name": code, "level": level, "is_basement": basement,
            "gross_floor_area_m2": "1000"}).json()["id"]
    room = client.post(f"/api/projects/{pid}/rooms", json={"floor_id": floors["L01"], "code": "R101", "name": "Lobby"},
                       headers=h).json()["id"]
    v = client.post(f"/api/projects/{pid}/estimates", headers=h,
                    json={"code": "E", "name": "E", "pricing_date": "2026-11-01"}).json()["version_id"]
    url = f"/api/projects/{pid}/versions/{v}"
    for code, fid in floors.items():
        assert client.post(f"{url}/items", headers=h, json={**TILE, "item_key": f"tile-{code}", "building_id": b,
                                                            "floor_id": fid}).status_code == 200
    assert client.post(f"{url}/items", headers=h, json={**TILE, "item_key": "tile-R101", "building_id": b,
                                                        "floor_id": floors["L01"], "room_id": room}).status_code == 200
    by_floor = {r["floor"]: r for r in client.get(f"{url}/consolidate", params={"by": "floor"}, headers=h).json()}
    assert by_floor["L01"]["items"] == 2 and by_floor["B1"]["items"] == 1
    basement = {r["basement"]: r["items"] for r in client.get(f"{url}/consolidate", params={"by": "basement"}, headers=h).json()}
    assert basement == {"basement": 1, "above grade": 3}
    per_area = client.get(f"{url}/summary", headers=h).json()["cost_per_area"]
    assert per_area["gross_floor_area_m2"] == "3000"
    assert per_area["cost_per_m2"].startswith("83.445")  # 4 × 62,584.43 ÷ 3,000 m²
    assert client.get(f"{url}/consolidate", params={"by": "colour"}, headers=h).status_code == 422

    # A room from another project is rejected by the database's composite keys.
    other = client.post("/api/projects", json={"code": "X", "name": "X", "currency": "PHP"}, headers=h).json()["id"]
    xb = client.post(f"/api/projects/{other}/buildings", json={"code": "T", "name": "T"}, headers=h).json()["id"]
    r = client.post(f"{url}/items", headers=h, json={**TILE, "item_key": "bad", "building_id": xb})
    assert r.status_code == 422


def test_provisional_sum_through_the_api(client):
    h, ce = auth(client, "est@cep.test"), auth(client, "ce@cep.test")
    pid = client.post("/api/projects", json={"code": "P", "name": "P", "currency": "PHP"}, headers=h).json()["id"]
    # Project manager records the project's provisional-sum amount as a dated price record.
    assert client.post("/api/library/materials", headers=h, json={"code": "PS-LIFTS", "description": "Lifts (provisional)",
                                                                  "unit": "lot", "category": "other",
                                                                  "project_id": pid}).status_code == 201
    assert client.post("/api/library/prices", headers=h, json={"material_code": "PS-LIFTS", "unit": "lot", "price": "12500000",
                                                               "currency": "PHP", "valid_from": "2026-01-01",
                                                               "project_id": pid}).status_code == 201
    assert ce  # cost engineers are not needed for project-scoped records
    v = client.post(f"/api/projects/{pid}/estimates", headers=h,
                    json={"code": "E", "name": "E", "pricing_date": "2026-11-01"}).json()["version_id"]
    url = f"/api/projects/{pid}/versions/{v}"
    r = client.post(f"{url}/items", headers=h, json={"item_key": "ps-lifts", "module": "provisional_sums", "item_type": "allowance",
                                                     "inputs": {}, "priced_item_code": "PS-LIFTS"})
    assert r.status_code == 200, r.text
    # 12.5 M direct, then the configured markups: × 1.25 × 1.12
    assert r.json()["amount"].startswith("17500000")
    assert client.get(f"{url}/boq", headers=h).json()[0]["assembly_code"] == "PRICED:PS-LIFTS"
    assert client.post(f"{url}/approve", headers=h).status_code == 200
    v2 = client.post(f"{url}/revisions", json={}, headers=h).json()["version_id"]
    items = client.get(f"/api/projects/{pid}/versions/{v2}/items", headers=h).json()
    assert items[0]["amount"].startswith("17500000")
