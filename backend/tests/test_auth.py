"""Password changes, administrator reset links, lockout, and OpenID Connect single sign-on."""
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import sso

from .test_api import PW, auth, client  # noqa: F401  (fixture)

NEW_PW = "a-brand-new-passphrase"


def test_lockout_after_repeated_failures_and_admin_unlock(client, monkeypatch):  # noqa: F811
    from app.services import users

    monkeypatch.setattr(users, "MAX_FAILED_LOGINS", 3)
    bad = {"email": "est@cep.test", "password": "wrong-password-x"}
    for _ in range(3):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    r = client.post("/api/auth/login", json={"email": "est@cep.test", "password": PW})
    assert r.status_code == 429  # locked even with the right password
    admin = auth(client, "admin@cep.test")
    uid = next(u["id"] for u in client.get("/api/users", headers=admin).json() if u["email"] == "est@cep.test")
    assert next(u for u in client.get("/api/users", headers=admin).json() if u["id"] == uid)["locked"]
    assert client.post(f"/api/users/{uid}/unlock", headers=auth(client, "view@cep.test")).status_code == 403
    assert client.post(f"/api/users/{uid}/unlock", headers=admin).status_code == 204
    assert client.post("/api/auth/login", json={"email": "est@cep.test", "password": PW}).status_code == 200


def test_change_password_signs_out_other_sessions(client):  # noqa: F811
    old = auth(client, "est@cep.test")
    other = auth(client, "est@cep.test")
    r = client.post("/api/auth/password", headers=old, json={"current_password": "nope-nope-nope", "new_password": NEW_PW})
    assert r.status_code == 422
    assert client.post("/api/auth/password", headers=old, json={"current_password": PW, "new_password": "short"}).status_code == 422
    r = client.post("/api/auth/password", headers=old, json={"current_password": PW, "new_password": NEW_PW})
    assert r.status_code == 200
    fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.post("/api/auth/login", json={"email": "est@cep.test", "password": NEW_PW}).status_code == 200


def test_admin_reset_link_is_single_use(client):  # noqa: F811
    admin = auth(client, "admin@cep.test")
    uid = next(u["id"] for u in client.get("/api/users", headers=admin).json() if u["email"] == "view@cep.test")
    assert client.post(f"/api/users/{uid}/reset-link", headers=auth(client, "est@cep.test")).status_code == 403
    link = client.post(f"/api/users/{uid}/reset-link", headers=admin).json()
    assert link["path"].startswith("/reset-password?token=")
    assert client.post("/api/auth/reset", json={"token": link["token"], "new_password": "short"}).status_code == 422
    r = client.post("/api/auth/reset", json={"token": link["token"], "new_password": NEW_PW})
    assert r.status_code == 200 and r.json()["user"]["email"] == "view@cep.test"
    assert client.post("/api/auth/reset", json={"token": link["token"], "new_password": NEW_PW}).status_code == 422
    assert client.post("/api/auth/reset", json={"token": "made-up", "new_password": NEW_PW}).status_code == 422
    assert client.post("/api/auth/login", json={"email": "view@cep.test", "password": NEW_PW}).status_code == 200


# --------------------------------------------------------------------------- single sign-on against a simulated provider

ISSUER = "https://idp.example.test"


class FakeIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.claims: dict = {}
        self.last_form: dict = {}

    def get(self, url):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize", "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks"}
        assert url == f"{ISSUER}/jwks"
        return self.jwks

    def post(self, url, data):
        assert url == f"{ISSUER}/token" and data["code"] == "the-code" and data["code_verifier"]
        self.last_form = data
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "cep-web", "sub": "u-123", "iat": now, "exp": now + 300, **self.claims}
        return {"id_token": jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})}


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setenv("CEP_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CEP_OIDC_CLIENT_ID", "cep-web")
    monkeypatch.setenv("CEP_OIDC_REDIRECT_URI", "http://localhost:3000/login/sso")
    monkeypatch.delenv("CEP_OIDC_AUTO_CREATE", raising=False)
    monkeypatch.setattr(sso, "http_get_json", fake.get)
    monkeypatch.setattr(sso, "http_post_form", fake.post)
    sso._cache.clear()
    return fake


def _start(client):  # noqa: F811
    url = client.post("/api/auth/sso/start").json()["authorization_url"]
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cep-web"]
    return q["state"][0], q["nonce"][0]


def test_sso_is_off_until_configured(client, monkeypatch):  # noqa: F811
    monkeypatch.delenv("CEP_OIDC_ISSUER", raising=False)
    info = client.get("/api/auth/sso").json()
    assert info["enabled"] is False and info["label"] is None
    assert client.post("/api/auth/sso/start").status_code == 404


def test_sso_signs_in_an_existing_account_and_links_it(client, idp):  # noqa: F811
    assert client.get("/api/auth/sso").json()["enabled"]
    state, nonce = _start(client)
    idp.claims = {"email": "EST@cep.test", "email_verified": True, "nonce": nonce}
    r = client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "est@cep.test"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    # the state is single use
    assert client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state}).status_code == 401
    # later sign-ins match the provider's subject, even if the email changes there
    state, nonce = _start(client)
    idp.claims = {"email": "renamed@cep.test", "nonce": nonce}
    assert client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state}).json()["user"]["email"] == "est@cep.test"


def test_sso_rejects_bad_tokens_and_unknown_people(client, idp):  # noqa: F811
    state, _ = _start(client)
    idp.claims = {"email": "est@cep.test", "nonce": "someone-elses-nonce"}
    assert "does not belong" in client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state}).json()["detail"]

    state, nonce = _start(client)
    idp.claims = {"email": "est@cep.test", "nonce": nonce, "aud": "another-app"}
    assert "not valid" in client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state}).json()["detail"]

    state, nonce = _start(client)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    real_post = idp.post

    def forged(url, data):
        good = jwt.decode(real_post(url, data)["id_token"], options={"verify_signature": False})
        return {"id_token": jwt.encode(good, other_key, algorithm="RS256", headers={"kid": "k1"})}

    idp.post = forged
    idp.claims = {"email": "est@cep.test", "nonce": nonce}
    sso.http_post_form = forged
    assert client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state}).status_code == 401
    sso.http_post_form = real_post

    state, nonce = _start(client)
    idp.claims = {"email": "stranger@elsewhere.test", "email_verified": True, "nonce": nonce}
    r = client.post("/api/auth/sso/callback", json={"code": "the-code", "state": state})
    assert r.status_code == 401 and "no CEP account" in r.json()["detail"]
