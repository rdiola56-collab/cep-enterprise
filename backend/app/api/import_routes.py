"""HTTP API for drawing and BIM quantity import. Thin: the pipeline lives in services/imports.py."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import jobs
from ..auth.permissions import Perm
from ..db import models as m
from ..integrations import ADAPTERS, BACKGROUND_ONLY, SourceError, adapter_info
from ..services import imports as imp
from .deps import DB, CurrentUser, project_access
from .routes import _guard, _version, jsonable

router = APIRouter()

MAX_UPLOAD = 200 * 1024 * 1024  # bytes


@router.get("/import/adapters")
def adapters(user: CurrentUser):
    return adapter_info()


class RuleIn(BaseModel):
    code: str
    adapter: str
    match_group: str
    match_label: str | None = None
    match_properties: dict[str, str] = Field(default_factory=dict)
    module: str
    item_type: str
    assembly_code: str | None = None
    priced_item_code: str | None = None
    quantity_basis: str = "measured"
    input_map: dict[str, str | dict]
    condition: str = "normal"
    aggregate: bool = False
    priority: int = 100


@router.get("/projects/{project_id}/import-rules")
def list_rules(project_id: uuid.UUID, user: CurrentUser, s: DB, adapter: str | None = None, include_inactive: bool = False):
    project_access(s, user, project_id)
    return jsonable([imp.rule_dict(r) for r in imp.rules(s, project_id, adapter, active_only=not include_inactive)])


@router.post("/projects/{project_id}/import-rules", status_code=201)
def create_rule(project_id: uuid.UUID, body: RuleIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    return jsonable(imp.rule_dict(_guard(imp.create_rule, s, project_id, body.model_dump(), user.email)))


@router.delete("/projects/{project_id}/import-rules/{rule_id}", status_code=204)
def deactivate_rule(project_id: uuid.UUID, rule_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _guard(imp.deactivate_rule, s, project_id, rule_id, user.email)


def _batch_out(s, b, rows: bool = True, status: str | None = None) -> dict:
    out = imp.batch_dict(b)
    if rows:
        out["rows"] = [imp.row_dict(r) for r in imp.batch_rows(s, b.id, status)]
    return jsonable(out)


def _guard_import(fn, *args, **kwargs):
    try:
        return _guard(fn, *args, **kwargs)
    except imp.ImportBlocked as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/projects/{project_id}/versions/{version_id}/imports", status_code=201)
async def stage_import(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, s: DB,
                       file: UploadFile = File(...), adapter: str = Form(...), source_name: str = Form(...),
                       source_revision: str = Form(...), level: str | None = Form(None),
                       drawing_unit: str | None = Form(None), default_unit: str | None = Form(None),
                       building_id: uuid.UUID | None = Form(None), background: bool = Form(False)):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _version(s, project_id, version_id)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 200 MB")
    options = {k: v for k, v in {"level": level, "drawing_unit": drawing_unit, "default_unit": default_unit}.items() if v}
    if adapter == "pdf_takeoff":
        raise HTTPException(422, "PDF drawings are measured on screen: upload them under Drawings")
    if adapter not in ADAPTERS:
        raise HTTPException(422, f"no importer for '{adapter}'")
    if background or adapter in BACKGROUND_ONLY:
        job = jobs.create(s, project_id, "import_stage", {
            "version_id": str(version_id), "adapter": adapter, "file_name": file.filename or "upload",
            "source_name": source_name, "source_revision": source_revision, "options": options,
            "building_id": str(building_id) if building_id else None}, user.email, payload=content)
        return JSONResponse(jsonable(jobs.job_dict(jobs.dispatch(s, job))), status_code=202)
    try:
        b = _guard_import(imp.stage, s, version_id, adapter=adapter, file_name=file.filename or "upload", content=content,
                          source_name=source_name, source_revision=source_revision, options=options,
                          building_id=building_id, actor=user.email, project_id=project_id)
    except SourceError as exc:
        raise HTTPException(422, f"file not imported: {exc}") from None
    return _batch_out(s, b)


@router.get("/projects/{project_id}/imports")
def list_imports(project_id: uuid.UUID, user: CurrentUser, s: DB, version_id: uuid.UUID | None = None):
    project_access(s, user, project_id)
    from sqlalchemy import select

    from ..db import models as m
    q = select(m.ImportBatch).where(m.ImportBatch.project_id == project_id)
    if version_id:
        q = q.where(m.ImportBatch.estimate_version_id == version_id)
    return jsonable([imp.batch_dict(b) for b in s.scalars(q.order_by(m.ImportBatch.created_at.desc()))])


@router.get("/projects/{project_id}/imports/{batch_id}")
def get_import(project_id: uuid.UUID, batch_id: uuid.UUID, user: CurrentUser, s: DB, status: str | None = None):
    project_access(s, user, project_id)
    return _batch_out(s, _guard(imp.get_batch, s, project_id, batch_id), status=status)


@router.post("/projects/{project_id}/imports/{batch_id}/remap")
def remap(project_id: uuid.UUID, batch_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    b = _guard(imp.get_batch, s, project_id, batch_id)
    return _batch_out(s, _guard_import(imp.apply_rules, s, b.id))


class ExcludeIn(BaseModel):
    row_ids: list[uuid.UUID]
    excluded: bool = True
    reason: str = ""


@router.post("/projects/{project_id}/imports/{batch_id}/exclude")
def exclude(project_id: uuid.UUID, batch_id: uuid.UUID, body: ExcludeIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    b = _guard(imp.get_batch, s, project_id, batch_id)
    return _batch_out(s, _guard_import(imp.set_excluded, s, b.id, body.row_ids, body.excluded, body.reason, user.email))


class CommitIn(BaseModel):
    remove_missing: bool = False


@router.post("/projects/{project_id}/imports/{batch_id}/commit")
def commit(project_id: uuid.UUID, batch_id: uuid.UUID, body: CommitIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    b = _guard(imp.get_batch, s, project_id, batch_id)
    with s.begin_nested():  # all items or none
        b = _guard_import(imp.commit, s, b.id, user.email, body.remove_missing)
    return _batch_out(s, b)


@router.post("/projects/{project_id}/imports/{batch_id}/discard", status_code=204)
def discard(project_id: uuid.UUID, batch_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    b = _guard(imp.get_batch, s, project_id, batch_id)
    _guard_import(imp.discard, s, b.id, user.email)


# ---------------------------------------------------------------- background jobs


class ExportJobIn(BaseModel):
    format: str = Field(pattern="^(xlsx|pdf)$")


@router.post("/projects/{project_id}/versions/{version_id}/export-jobs", status_code=202)
def export_job(project_id: uuid.UUID, version_id: uuid.UUID, body: ExportJobIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    _version(s, project_id, version_id)
    job = jobs.create(s, project_id, "export", {"version_id": str(version_id), "format": body.format}, user.email)
    return jsonable(jobs.job_dict(jobs.dispatch(s, job)))


def _job(s, project_id: uuid.UUID, job_id: uuid.UUID) -> m.Job:
    job = s.get(m.Job, job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(404, "job not found")
    return job


@router.get("/projects/{project_id}/jobs/{job_id}")
def get_job(project_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    return jsonable(jobs.job_dict(_job(s, project_id, job_id)))


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: uuid.UUID, user: CurrentUser, s: DB, limit: int = 20):
    project_access(s, user, project_id)
    from sqlalchemy import select
    q = select(m.Job).where(m.Job.project_id == project_id).order_by(m.Job.created_at.desc()).limit(min(limit, 100))
    return jsonable([jobs.job_dict(j) for j in s.scalars(q)])


@router.get("/projects/{project_id}/jobs/{job_id}/download")
def download_job(project_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    job = _job(s, project_id, job_id)
    if job.status != "succeeded" or job.output is None:
        raise HTTPException(409, f"job is {job.status}; nothing to download")
    return Response(job.output, media_type=job.output_type,
                    headers={"Content-Disposition": f'attachment; filename="{job.output_name}"'})
