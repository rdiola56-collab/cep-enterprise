"""SAML 2.0 single sign-on against a simulated identity provider that signs real assertions (xmlsec)."""
import base64
import datetime as dt
import uuid
import zlib
from urllib.parse import parse_qs, quote, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from app.auth import saml

from .test_api import client  # noqa: F401  (fixture)

IDP = "https://idp.example.test/saml"
SP_BASE = "https://cep.example.test"
ACS = f"{SP_BASE}/api/auth/saml/acs"
AUDIENCE = f"{SP_BASE}/api/auth/saml/metadata"


def _keypair(cn="idp.example.test"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            cert.public_bytes(serialization.Encoding.PEM).decode(), base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode())


class FakeIdP:
    def __init__(self):
        self.key, self.cert, self.cert_b64 = _keypair()

    def metadata(self) -> str:
        return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{IDP}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:X509Data>
      <ds:X509Certificate>{self.cert_b64}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.test/sso"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""

    def response(self, in_response_to: str, nameid: str, email: str | None = None, audience: str = AUDIENCE,
                 minutes_valid: int = 5, sign: bool = True, key: str | None = None, cert: str | None = None,
                 tamper=None) -> str:
        from onelogin.saml2.constants import OneLogin_Saml2_Constants as C
        from onelogin.saml2.utils import OneLogin_Saml2_Utils

        now = dt.datetime.now(dt.timezone.utc)
        t = lambda d: (now + d).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        attrs = (f'<saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue>'
                 f'</saml:Attribute><saml:Attribute Name="displayName"><saml:AttributeValue>Est Imator</saml:AttributeValue>'
                 f'</saml:Attribute></saml:AttributeStatement>') if email else ""
        assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{t(dt.timedelta())}"><saml:Issuer>{IDP}</saml:Issuer><saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">{nameid}</saml:NameID><saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData NotOnOrAfter="{t(dt.timedelta(minutes=minutes_valid))}" Recipient="{ACS}" InResponseTo="{in_response_to}"/></saml:SubjectConfirmation></saml:Subject><saml:Conditions NotBefore="{t(dt.timedelta(minutes=-1))}" NotOnOrAfter="{t(dt.timedelta(minutes=minutes_valid))}"><saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions><saml:AuthnStatement AuthnInstant="{t(dt.timedelta())}" SessionIndex="_s1"><saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>{attrs}</saml:Assertion>"""
        if sign:
            signed = OneLogin_Saml2_Utils.add_sign(assertion, key or self.key, cert or self.cert,
                                                   sign_algorithm=C.RSA_SHA256, digest_algorithm=C.SHA256)
            assertion = signed.decode() if isinstance(signed, bytes) else signed
            assertion = assertion.split("?>", 1)[1] if assertion.startswith("<?xml") else assertion
        if tamper:
            assertion = tamper(assertion)
        xml = (f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
               f'ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{t(dt.timedelta())}" Destination="{ACS}" '
               f'InResponseTo="{in_response_to}"><saml:Issuer>{IDP}</saml:Issuer><samlp:Status><samlp:StatusCode '
               f'Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>{assertion}</samlp:Response>')
        return base64.b64encode(xml.encode()).decode()


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setenv("CEP_SAML_IDP_METADATA_URL", "https://idp.example.test/metadata")
    monkeypatch.setenv("CEP_SAML_SP_BASE_URL", SP_BASE)
    monkeypatch.setenv("CEP_APP_URL", "https://cep.example.test")
    monkeypatch.delenv("CEP_SAML_AUTO_CREATE", raising=False)
    monkeypatch.setattr(saml, "http_get", lambda url: fake.metadata())
    saml._cache.clear()
    return fake


def _start(client):  # noqa: F811
    url = client.post("/api/auth/saml/start").json()["redirect_url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://idp.example.test/sso?")
    request = etree.fromstring(zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15))
    assert request.get("AssertionConsumerServiceURL") == ACS
    return request.get("ID")


def _post(client, saml_response):  # noqa: F811
    r = client.post("/api/auth/saml/acs", data={"SAMLResponse": saml_response}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def _error(location):
    assert location.startswith("https://cep.example.test/login?sso_error="), location
    return parse_qs(urlparse(location).query)["sso_error"][0]


def test_saml_is_off_until_configured(client, monkeypatch):  # noqa: F811
    monkeypatch.delenv("CEP_SAML_IDP_METADATA_URL", raising=False)
    monkeypatch.delenv("CEP_SAML_IDP_METADATA_FILE", raising=False)
    assert client.get("/api/auth/sso").json()["saml"] == {"enabled": False, "label": None}
    assert client.post("/api/auth/saml/start").status_code == 404
    assert client.get("/api/auth/saml/metadata").status_code == 404


def test_saml_signs_in_and_links_the_account(client, idp):  # noqa: F811
    assert client.get("/api/auth/sso").json()["saml"] == {"enabled": True, "label": "Sign in with SAML"}
    meta = client.get("/api/auth/saml/metadata")
    assert meta.status_code == 200 and ACS in meta.text and f'entityID="{AUDIENCE}"' in meta.text

    rid = _start(client)
    loc = _post(client, idp.response(rid, "emp-1001", "EST@cep.test"))
    assert loc.startswith("https://cep.example.test/login/sso#handoff="), loc
    code = loc.split("#handoff=", 1)[1]
    r = client.post("/api/auth/saml/redeem", json={"code": code})
    assert r.status_code == 200 and r.json()["user"]["email"] == "est@cep.test"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert client.post("/api/auth/saml/redeem", json={"code": code}).status_code == 401  # one use

    # The same response cannot be used again.
    assert "already used" in _error(_post(client, idp.response(rid, "emp-1001", "est@cep.test")))
    # Later sign-ins follow the IdP's NameID, even when the email attribute changes there.
    rid = _start(client)
    loc = _post(client, idp.response(rid, "emp-1001", "renamed@cep.test"))
    r = client.post("/api/auth/saml/redeem", json={"code": loc.split("#handoff=", 1)[1]})
    assert r.json()["user"]["email"] == "est@cep.test"


def test_saml_rejects_what_it_must(client, idp):  # noqa: F811
    other_key, other_cert, _ = _keypair("evil.example.test")
    expected = {"unsolicited": "does not answer a sign-in started here", "wrong audience": "not a valid audience",
                "expired": "SubjectConfirmation", "unsigned": "not signed", "signed by someone else": "Signature validation failed",
                "tampered after signing": "Signature validation failed", "unknown person": "no CEP account"}
    cases = {
        "unsolicited": lambda rid: idp.response("_never-asked", "emp-1", "est@cep.test"),
        "wrong audience": lambda rid: idp.response(rid, "emp-1", "est@cep.test", audience="https://other.example.test"),
        "expired": lambda rid: idp.response(rid, "emp-1", "est@cep.test", minutes_valid=-3),
        "unsigned": lambda rid: idp.response(rid, "emp-1", "est@cep.test", sign=False),
        "signed by someone else": lambda rid: idp.response(rid, "emp-1", "est@cep.test", key=other_key, cert=other_cert),
        "tampered after signing": lambda rid: idp.response(rid, "emp-1", "view@cep.test",
                                                           tamper=lambda a: a.replace("view@cep.test", "est@cep.test")),
        "unknown person": lambda rid: idp.response(rid, "emp-9", "stranger@elsewhere.test"),
    }
    for what, make in cases.items():
        rid = _start(client)
        message = _error(_post(client, make(rid)))
        assert expected[what] in message, (what, message)
    assert client.post("/api/auth/saml/acs", data={"SAMLResponse": quote("not base64 xml")},
                       follow_redirects=False).status_code == 303
    assert client.post("/api/auth/saml/redeem", json={"code": "made-up"}).status_code == 401
