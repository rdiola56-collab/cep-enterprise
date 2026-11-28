"""Revit models (.rvt), read through Autodesk Platform Services (APS).

Revit's file format is closed, and nothing but Autodesk's own software can read
it. So CEP sends the model to the APS Model Derivative service with the
company's own APS app, has APS export it to IFC (using Revit's own IFC
exporter), downloads that IFC and measures it with the IFC adapter. The quantities,
units, storeys and GlobalIds are therefore exactly what an IFC export from
Revit gives, and the IFC mapping rules serve both.

It is off until configured (an APS app from https://aps.autodesk.com):

    CEP_APS_CLIENT_ID, CEP_APS_CLIENT_SECRET   the APS app's credentials
    CEP_APS_BUCKET        optional OSS bucket key (default derived from the client id)
    CEP_APS_REGION        US (default) or EMEA
    CEP_APS_IFC_SETTING   optional Revit IFC export setup name, e.g. "IFC2x3 Coordination View 2.0"
    CEP_APS_TIMEOUT       seconds to wait for the translation (default 1800)

The model file leaves this server: it is uploaded to the company's APS account
(a transient bucket, which APS deletes after 24 hours). Translations can take
minutes, so Revit imports always run as background jobs.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from urllib.parse import quote

from . import ifc
from .base import ParsedSource, SourceError

BASE = "https://developer.api.autodesk.com"
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")  # Revit files are OLE compound documents
SCOPES = "data:read data:write data:create bucket:create bucket:read"
POLL_S = 5


@dataclass(frozen=True)
class ApsConfig:
    client_id: str
    client_secret: str
    bucket: str
    region: str
    ifc_setting: str | None
    timeout_s: int


def config() -> ApsConfig | None:
    cid, secret = os.environ.get("CEP_APS_CLIENT_ID"), os.environ.get("CEP_APS_CLIENT_SECRET")
    if not cid or not secret:
        return None
    region = (os.environ.get("CEP_APS_REGION") or "US").upper()
    if region not in ("US", "EMEA"):
        region = "US"
    bucket = os.environ.get("CEP_APS_BUCKET") or f"cep-{hashlib.sha256(cid.encode()).hexdigest()[:24]}"
    return ApsConfig(cid, secret, bucket.lower(), region, os.environ.get("CEP_APS_IFC_SETTING") or None,
                     int(os.environ.get("CEP_APS_TIMEOUT", "1800")))


def status() -> str:
    return "operational" if config() else "needs setup: an Autodesk Platform Services app (CEP_APS_CLIENT_ID / SECRET)"


# ------------------------------------------------------------------ HTTP (replaced by a simulated APS in tests)


def http(method: str, url: str, *, headers: dict | None = None, json: dict | None = None, data=None,
         content: bytes | None = None) -> tuple[int, dict, bytes]:
    import httpx

    r = httpx.request(method, url, headers=headers, json=json, data=data, content=content, timeout=120)
    return r.status_code, {k.lower(): v for k, v in r.headers.multi_items() if k.lower() != "set-cookie"} | {
        "set-cookie": r.headers.get_list("set-cookie")}, r.content


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def _json(status: int, body: bytes, what: str) -> dict:
    import json

    if status >= 400:
        detail = body.decode("utf-8", "replace")[:300]
        raise SourceError(f"Autodesk Platform Services refused {what} ({status}: {detail})")
    try:
        return json.loads(body or b"{}")
    except ValueError:
        raise SourceError(f"Autodesk Platform Services sent an unreadable answer to {what}") from None


# ------------------------------------------------------------------ the translation


def _token(cfg: ApsConfig) -> str:
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    st, _, body = http("POST", f"{BASE}/authentication/v2/token",
                       headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
                       data={"grant_type": "client_credentials", "scope": SCOPES})
    return _json(st, body, "the app's credentials")["access_token"]


def _md_base(cfg: ApsConfig) -> str:
    return f"{BASE}/modelderivative/v2/regions/eu/designdata" if cfg.region == "EMEA" else f"{BASE}/modelderivative/v2/designdata"


def urn_for(object_id: str) -> str:
    return base64.urlsafe_b64encode(object_id.encode()).decode().rstrip("=")


def translate_to_ifc(content: bytes, cfg: ApsConfig) -> tuple[bytes, dict]:
    """Upload a Revit model, have APS export it to IFC, and return the IFC bytes and what happened."""
    auth = {"Authorization": f"Bearer {_token(cfg)}"}

    st, _, body = http("POST", f"{BASE}/oss/v2/buckets", headers={**auth, "x-ads-region": cfg.region},
                       json={"bucketKey": cfg.bucket, "policyKey": "transient"})
    if st != 409:  # 409: the bucket already exists
        _json(st, body, "creating the upload bucket")

    key = f"{hashlib.sha256(content).hexdigest()}.rvt"
    obj = f"{BASE}/oss/v2/buckets/{quote(cfg.bucket)}/objects/{quote(key)}/signeds3upload"
    up = _json(*http("GET", f"{obj}?minutesExpiration=60", headers=auth)[::2], "the upload")
    st, _, body = http("PUT", up["urls"][0], content=content)
    if st >= 400:
        raise SourceError(f"uploading the model to Autodesk failed ({st})")
    done = _json(*http("POST", obj, headers=auth, json={"uploadKey": up["uploadKey"]})[::2], "completing the upload")
    urn = urn_for(done["objectId"])

    fmt: dict = {"type": "ifc"}
    if cfg.ifc_setting:
        fmt["advanced"] = {"exportSettingName": cfg.ifc_setting}
    _json(*http("POST", f"{_md_base(cfg)}/job", headers={**auth, "x-ads-force": "true"},
                json={"input": {"urn": urn}, "output": {"formats": [fmt]}})[::2], "the IFC export job")

    waited = 0
    while True:
        manifest = _json(*http("GET", f"{_md_base(cfg)}/{urn}/manifest", headers=auth)[::2], "the export status")
        state = manifest.get("status")
        if state == "success":
            break
        if state in ("failed", "timeout"):
            msgs = [m.get("message") for d in manifest.get("derivatives", []) for m in d.get("messages", []) if m.get("message")]
            raise SourceError("Autodesk could not export this model to IFC" + (f": {'; '.join(msgs)}" if msgs else ""))
        if waited >= cfg.timeout_s:
            raise SourceError(f"the IFC export was not finished after {cfg.timeout_s} s (APS status: {state})")
        sleep(POLL_S)
        waited += POLL_S

    files = [c for d in manifest.get("derivatives", []) if d.get("outputType") == "ifc"
             for c in d.get("children", []) if str(c.get("urn", "")).lower().endswith(".ifc")]
    if not files:
        raise SourceError("Autodesk finished the export but produced no IFC file")
    derivative = files[0]["urn"]
    st, hdrs, body = http("GET", f"{_md_base(cfg)}/{urn}/manifest/{quote(derivative, safe='')}/signedcookies", headers=auth)
    signed = _json(st, body, "the IFC download")
    cookies = "; ".join(c.split(";", 1)[0] for c in hdrs.get("set-cookie", []))
    st, _, ifc_bytes = http("GET", signed["url"], headers={"Cookie": cookies} if cookies else {})
    if st >= 400 or not ifc_bytes:
        raise SourceError(f"downloading the IFC export from Autodesk failed ({st})")
    return ifc_bytes, {"aps_urn": urn, "aps_object": done["objectId"], "ifc_bytes": len(ifc_bytes),
                       "ifc_export_setting": cfg.ifc_setting or "Revit default"}


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    if not content.startswith(OLE_SIGNATURE):
        raise SourceError("not a Revit model (.rvt files are OLE compound documents)")
    cfg = config()
    if cfg is None:
        raise SourceError("Revit reading needs an Autodesk Platform Services app, which is not set up on this server; "
                          "export IFC from Revit instead, or ask an administrator to set CEP_APS_CLIENT_ID and CEP_APS_CLIENT_SECRET")
    ifc_bytes, facts = translate_to_ifc(content, cfg)
    parsed = ifc.parse(ifc_bytes, options)
    parsed.adapter = "rvt"
    parsed.info = {**parsed.info, **facts, "read_by": "Autodesk Platform Services Model Derivative (Revit → IFC)"}
    return parsed
