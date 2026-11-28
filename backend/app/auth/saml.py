"""Single sign-on with SAML 2.0 (SP-initiated, HTTP-Redirect request, HTTP-POST response).

For identity providers that offer SAML rather than OpenID Connect (ADFS, Okta, Entra ID,
Google Workspace, OneLogin, Shibboleth…). Signatures, audience, destination, validity
window and the reply-to request are checked by python3-saml (OneLogin, xmlsec). Off until
configured:

    CEP_SAML_IDP_METADATA_URL   the IdP's metadata URL  (or CEP_SAML_IDP_METADATA_FILE, a saved copy)
    CEP_SAML_SP_BASE_URL        where this API is reached, e.g. https://cep.example.com
                                (ACS: <base>/api/auth/saml/acs, entity id and metadata: <base>/api/auth/saml/metadata)
    CEP_APP_URL                 the web app, to return the browser to (defaults to the SP base URL)
    CEP_SAML_LABEL              button text, default "Sign in with SAML"
    CEP_SAML_EMAIL_ATTRIBUTE    attribute holding the email, if the NameID is not the email
    CEP_SAML_AUTO_CREATE        a global role to create unknown users with; unset = refuse them
    CEP_SAML_SP_CERT, CEP_SAML_SP_KEY   optional PEM files: sign the requests CEP sends

Only responses to a request CEP sent are accepted (no IdP-initiated sign-in), each once.
Assertions must be signed with the IdP's certificate from its metadata. Accounts are
linked the same way as OpenID Connect: by email the first time, by IdP + NameID after.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import LoginHandoff, SamlRequest, User
from .permissions import GLOBAL_ROLES

REQUEST_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(seconds=120)
EMAIL_ATTRIBUTES = ("email", "mail", "emailaddress", "Email",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "urn:oid:0.9.2342.19200300.100.1.3")
NAME_ATTRIBUTES = ("displayName", "name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
                   "urn:oid:2.16.840.1.113730.3.1.241")


class SamlError(ValueError):
    pass


@dataclass(frozen=True)
class SamlConfig:
    metadata_url: str | None
    metadata_file: str | None
    sp_base: str
    app_url: str
    label: str
    email_attribute: str | None
    auto_create_role: str | None
    sp_cert: str | None
    sp_key: str | None

    @property
    def acs_url(self) -> str:
        return f"{self.sp_base}/api/auth/saml/acs"

    @property
    def entity_id(self) -> str:
        return os.environ.get("CEP_SAML_SP_ENTITY_ID") or f"{self.sp_base}/api/auth/saml/metadata"


def config() -> SamlConfig | None:
    url, path, base = (os.environ.get(k) for k in ("CEP_SAML_IDP_METADATA_URL", "CEP_SAML_IDP_METADATA_FILE", "CEP_SAML_SP_BASE_URL"))
    if not ((url or path) and base):
        return None
    role = os.environ.get("CEP_SAML_AUTO_CREATE") or None
    if role and role not in GLOBAL_ROLES:
        raise SamlError(f"CEP_SAML_AUTO_CREATE must be one of {', '.join(GLOBAL_ROLES)}")
    cert, key = (_read(os.environ.get(k)) for k in ("CEP_SAML_SP_CERT", "CEP_SAML_SP_KEY"))
    if bool(cert) != bool(key):
        raise SamlError("set both CEP_SAML_SP_CERT and CEP_SAML_SP_KEY, or neither")
    return SamlConfig(url, path, base.rstrip("/"), (os.environ.get("CEP_APP_URL") or base).rstrip("/"),
                      os.environ.get("CEP_SAML_LABEL", "Sign in with SAML"), os.environ.get("CEP_SAML_EMAIL_ATTRIBUTE") or None,
                      role, cert, key)


def _read(path: str | None) -> str | None:
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- IdP metadata (replaceable in tests)

_cache: dict[str, tuple[float, dict]] = {}


def http_get(url: str) -> str:
    import httpx

    r = httpx.get(url, timeout=10, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _idp(cfg: SamlConfig) -> dict:
    from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser as Parser

    key = cfg.metadata_url or cfg.metadata_file
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        xml = http_get(cfg.metadata_url) if cfg.metadata_url else _read(cfg.metadata_file)
        parsed = Parser.parse(xml, required_sso_binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect")
    except Exception as exc:  # noqa: BLE001  (network, file or XML problems all mean "cannot use this IdP")
        raise SamlError(f"the identity provider's SAML metadata could not be read ({exc})") from None
    idp = parsed.get("idp") or {}
    if not idp.get("entityId") or not (idp.get("x509cert") or idp.get("x509certMulti")):
        raise SamlError("the identity provider's metadata has no entity id or signing certificate")
    _cache[key] = (time.time() + 3600, idp)
    return idp


def settings(cfg: SamlConfig) -> dict:
    return {
        "strict": True,
        "debug": False,
        "sp": {"entityId": cfg.entity_id,
               "assertionConsumerService": {"url": cfg.acs_url, "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"},
               "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
               "x509cert": cfg.sp_cert or "", "privateKey": cfg.sp_key or ""},
        "idp": _idp(cfg),
        "security": {"authnRequestsSigned": bool(cfg.sp_key), "wantAssertionsSigned": True, "wantMessagesSigned": False,
                     "wantNameId": True, "requestedAuthnContext": False, "rejectDeprecatedAlgorithm": True,
                     "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                     "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256"},
    }


def _request_data(cfg: SamlConfig, post: dict | None = None) -> dict:
    """python3-saml checks the Destination against the request URL; use the configured public ACS URL,
    not whatever host header a proxy forwarded."""
    u = urlparse(cfg.acs_url)
    return {"https": "on" if u.scheme == "https" else "off", "http_host": u.netloc, "script_name": u.path,
            "get_data": {}, "post_data": post or {}}


def metadata(cfg: SamlConfig) -> str:
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    st = OneLogin_Saml2_Settings(settings(cfg), sp_validation_only=True)
    xml = st.get_sp_metadata()
    return xml.decode() if isinstance(xml, bytes) else xml


# --------------------------------------------------------------------------- the flow


def start(s: Session, cfg: SamlConfig) -> str:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    auth = OneLogin_Saml2_Auth(_request_data(cfg), settings(cfg))
    url = auth.login()
    s.add(SamlRequest(request_id=auth.get_last_request_id(), expires_at=datetime.now(timezone.utc) + REQUEST_TTL))
    s.flush()
    return url


def _in_response_to(saml_response: str) -> str | None:
    from lxml import etree

    try:
        xml = base64.b64decode(saml_response, validate=False)
        root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    except Exception:  # noqa: BLE001
        raise SamlError("the SAML response is not readable") from None
    return root.get("InResponseTo")


def finish(s: Session, cfg: SamlConfig, saml_response: str, relay_state: str | None = None) -> User:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    rid = _in_response_to(saml_response)
    pending = rid and s.scalars(select(SamlRequest).where(SamlRequest.request_id == rid)).first()
    if not pending or pending.used_at is not None or pending.expires_at < datetime.now(timezone.utc):
        raise SamlError("this SAML response does not answer a sign-in started here, or was already used; start again")
    pending.used_at = datetime.now(timezone.utc)
    s.flush()

    post = {"SAMLResponse": saml_response, **({"RelayState": relay_state} if relay_state else {})}
    auth = OneLogin_Saml2_Auth(_request_data(cfg, post), settings(cfg))
    auth.process_response(request_id=rid)
    if auth.get_errors() or not auth.is_authenticated():
        raise SamlError(f"the SAML response was rejected ({auth.get_last_error_reason() or ', '.join(auth.get_errors())})")
    attrs = auth.get_attributes()
    nameid = auth.get_nameid()

    def first(names) -> str | None:
        for n in names:
            v = attrs.get(n)
            if v:
                return str(v[0])
        return None

    email = first([cfg.email_attribute]) if cfg.email_attribute else (first(EMAIL_ATTRIBUTES) or (nameid if "@" in (nameid or "") else None))
    claims = {"iss": f"saml:{settings(cfg)['idp']['entityId']}", "sub": nameid, "email": email, "email_verified": True,
              "name": first(NAME_ATTRIBUTES)}
    from .sso import SsoError, _account

    try:
        return _account(s, cfg, claims)
    except SsoError as exc:
        raise SamlError(str(exc)) from None


# --------------------------------------------------------------------------- handing the session to the web app


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def issue_handoff(s: Session, user: User) -> str:
    code = secrets.token_urlsafe(32)
    s.add(LoginHandoff(code_hash=_sha(code), user_id=user.id, expires_at=datetime.now(timezone.utc) + HANDOFF_TTL))
    s.flush()
    return code


def redeem(s: Session, code: str) -> User:
    h = s.scalars(select(LoginHandoff).where(LoginHandoff.code_hash == _sha(code))).first()
    if h is None or h.used_at is not None or h.expires_at < datetime.now(timezone.utc):
        raise SamlError("this sign-in link is unknown, used or expired; sign in again")
    h.used_at = datetime.now(timezone.utc)
    user = s.get(User, h.user_id)
    if user is None or not user.is_active:
        raise SamlError("this CEP account is deactivated")
    s.flush()
    return user
