"""Single sign-on with OpenID Connect (authorization code flow with PKCE).

Works with any standards-compliant provider (Microsoft Entra ID, Google Workspace,
Okta, Keycloak…). It is off until configured:

    CEP_OIDC_ISSUER         e.g. https://login.microsoftonline.com/<tenant>/v2.0
    CEP_OIDC_CLIENT_ID
    CEP_OIDC_CLIENT_SECRET  (optional for public clients)
    CEP_OIDC_REDIRECT_URI   e.g. https://cep.example.com/login/sso   (the web app's callback page)
    CEP_OIDC_LABEL          button text, default "Sign in with SSO"
    CEP_OIDC_AUTO_CREATE    a global role (e.g. "viewer") to create unknown users with; unset = refuse them

Accounts are matched by verified email the first time and by issuer+subject after that.
Signing in with SSO never grants project access by itself: project roles still come from CEP.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import SsoLoginState, User
from .permissions import GLOBAL_ROLES

STATE_TTL = timedelta(minutes=10)


class SsoError(ValueError):
    pass


@dataclass(frozen=True)
class SsoConfig:
    issuer: str
    client_id: str
    client_secret: str | None
    redirect_uri: str
    label: str
    auto_create_role: str | None


def config() -> SsoConfig | None:
    issuer, client_id, redirect = (os.environ.get(k) for k in ("CEP_OIDC_ISSUER", "CEP_OIDC_CLIENT_ID", "CEP_OIDC_REDIRECT_URI"))
    if not (issuer and client_id and redirect):
        return None
    role = os.environ.get("CEP_OIDC_AUTO_CREATE") or None
    if role and role not in GLOBAL_ROLES:
        raise SsoError(f"CEP_OIDC_AUTO_CREATE must be one of {', '.join(GLOBAL_ROLES)}")
    return SsoConfig(issuer.rstrip("/"), client_id, os.environ.get("CEP_OIDC_CLIENT_SECRET") or None, redirect,
                     os.environ.get("CEP_OIDC_LABEL", "Sign in with SSO"), role)


# --------------------------------------------------------------------------- HTTP (replaceable in tests)

_cache: dict[str, tuple[float, dict]] = {}


def http_get_json(url: str) -> dict:
    import httpx

    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def http_post_form(url: str, data: dict) -> dict:
    import httpx

    r = httpx.post(url, data=data, timeout=10, headers={"Accept": "application/json"})
    if r.status_code >= 400:
        raise SsoError(f"the identity provider refused the sign-in ({r.status_code})")
    return r.json()


def _discovery(cfg: SsoConfig) -> dict:
    hit = _cache.get(cfg.issuer)
    if hit and hit[0] > time.time():
        return hit[1]
    doc = http_get_json(f"{cfg.issuer}/.well-known/openid-configuration")
    if doc.get("issuer", "").rstrip("/") != cfg.issuer:
        raise SsoError("the provider's discovery document names a different issuer")
    _cache[cfg.issuer] = (time.time() + 3600, doc)
    return doc


# --------------------------------------------------------------------------- flow


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def start(s: Session, cfg: SsoConfig) -> str:
    """Create a pending login and return the provider's authorization URL."""
    doc = _discovery(cfg)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    s.add(SsoLoginState(state_hash=_sha(state), nonce=nonce, code_verifier=verifier,
                        expires_at=datetime.now(timezone.utc) + STATE_TTL))
    s.flush()
    query = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri,
             "scope": "openid email profile", "state": state, "nonce": nonce,
             "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{doc['authorization_endpoint']}?{urlencode(query)}"


def finish(s: Session, cfg: SsoConfig, code: str, state: str) -> User:
    """Exchange the code, verify the ID token, and return the CEP user it belongs to."""
    import jwt

    pending = s.scalars(select(SsoLoginState).where(SsoLoginState.state_hash == _sha(state))).first()
    if pending is None or pending.used_at is not None or pending.expires_at < datetime.now(timezone.utc):
        raise SsoError("this sign-in attempt is unknown or expired; start again")
    pending.used_at = datetime.now(timezone.utc)
    doc = _discovery(cfg)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id, "code_verifier": pending.code_verifier}
    if cfg.client_secret:
        form["client_secret"] = cfg.client_secret
    tokens = http_post_form(doc["token_endpoint"], form)
    raw = tokens.get("id_token")
    if not raw:
        raise SsoError("the identity provider returned no ID token")
    try:
        header = jwt.get_unverified_header(raw)
        keys = jwt.PyJWKSet.from_dict(http_get_json(doc["jwks_uri"]))
        key = next((k for k in keys.keys if k.key_id == header.get("kid")), None) or (keys.keys[0] if len(keys.keys) == 1 else None)
        if key is None:
            raise SsoError("the ID token is signed with an unknown key")
        claims = jwt.decode(raw, key=key.key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                            audience=cfg.client_id, issuer=doc["issuer"], options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                            leeway=60)
    except jwt.PyJWTError as exc:
        raise SsoError(f"the ID token is not valid ({exc})") from None
    if claims.get("nonce") != pending.nonce:
        raise SsoError("the ID token does not belong to this sign-in attempt")
    return _account(s, cfg, claims)


def _account(s: Session, cfg: SsoConfig, claims: dict) -> User:
    from ..services.audit import audit

    subject = f"{claims['iss']}|{claims['sub']}"
    user = s.scalars(select(User).where(User.sso_subject == subject)).first()
    if user is None:
        email = (claims.get("email") or "").strip().lower()
        if not email or claims.get("email_verified") is False:
            raise SsoError("the identity provider did not supply a verified email address")
        user = s.scalars(select(User).where(User.email == email)).first()
        if user is None:
            if not cfg.auto_create_role:
                raise SsoError(f"{email} has no CEP account; ask an administrator to create one")
            from ..services.users import create_user

            user = create_user(s, email, claims.get("name") or email, secrets.token_urlsafe(32), cfg.auto_create_role, "sso")
        if user.sso_subject is not None:
            raise SsoError("this CEP account is linked to a different single-sign-on identity")
        user.sso_subject = subject
        audit(s, None, "users", user.id, "sso_link", "sso", {"issuer": claims["iss"]})
    if not user.is_active:
        raise SsoError("this CEP account is deactivated")
    user.failed_logins, user.locked_until = 0, None
    s.flush()
    return user
