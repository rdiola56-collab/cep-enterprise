"""Password hashing (scrypt) and signed access tokens (HS256 JWT), standard library only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

log = logging.getLogger("cep.auth")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
TOKEN_TTL_SECONDS = int(os.environ.get("CEP_TOKEN_TTL_SECONDS", 8 * 3600))
MIN_PASSWORD_LENGTH = 10


def _secret() -> bytes:
    key = os.environ.get("CEP_SECRET_KEY")
    if key:
        return key.encode()
    global _EPHEMERAL
    if _EPHEMERAL is None:
        log.warning("CEP_SECRET_KEY is not set; using a random key. Tokens will not survive a restart.")
        _EPHEMERAL = secrets.token_bytes(32)
    return _EPHEMERAL


_EPHEMERAL: bytes | None = None


class AuthError(Exception):
    pass


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), digest)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(user_id: str, token_version: int, ttl: int = TOKEN_TTL_SECONDS) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64(json.dumps({"sub": user_id, "ver": token_version, "iat": now, "exp": now + ttl}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def read_token(token: str) -> dict:
    try:
        header, payload, sig = token.split(".")
    except ValueError:
        raise AuthError("malformed token") from None
    expected = _b64(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise AuthError("invalid token signature")
    try:
        if json.loads(_unb64(header)).get("alg") != "HS256":
            raise AuthError("unsupported token algorithm")
        claims = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("malformed token") from None
    if int(claims.get("exp", 0)) < time.time():
        raise AuthError("token expired")
    return claims
