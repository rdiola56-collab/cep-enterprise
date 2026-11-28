"""HTTP API for PDF drawings measured on screen. The logic lives in services/takeoff.py."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth.permissions import Perm
from ..db import models as m
from ..services import takeoff as tk
from .deps import DB, CurrentUser, project_access
from .import_routes import _batch_out, _guard_import
from .routes import _guard, _version, jsonable

router = APIRouter()

MAX_PDF = 200 * 1024 * 1024


def _sheet(s, project_id: uuid.UUID, sheet_id: uuid.UUID) -> m.DrawingSheet:
    return _guard(tk.get_sheet, s, project_id, sheet_id)


@router.post("/projects/{project_id}/sheets", status_code=201)
async def upload_sheet(project_id: uuid.UUID, user: CurrentUser, s: DB, file: UploadFile = File(...),
                       set_name: str = Form(...), revision: str = Form(...)):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    content = await file.read(MAX_PDF + 1)
    if len(content) > MAX_PDF:
        raise HTTPException(413, "file is larger than 200 MB")
    sheet = _guard(tk.upload, s, project_id, file_name=file.filename or "drawing.pdf", content=content,
                   set_name=set_name, revision=revision, actor=user.email)
    return jsonable(tk.sheet_dict(s, sheet, detail=True))


@router.get("/projects/{project_id}/sheets")
def list_sheets(project_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    q = select(m.DrawingSheet).where(m.DrawingSheet.project_id == project_id).order_by(m.DrawingSheet.created_at.desc())
    return jsonable([tk.sheet_dict(s, x) for x in s.scalars(q)])


@router.get("/projects/{project_id}/sheets/{sheet_id}")
def get_sheet(project_id: uuid.UUID, sheet_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    return jsonable(tk.sheet_dict(s, _sheet(s, project_id, sheet_id), detail=True))


@router.get("/projects/{project_id}/sheets/{sheet_id}/file")
def sheet_file(project_id: uuid.UUID, sheet_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    sheet = _sheet(s, project_id, sheet_id)
    return Response(sheet.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{sheet.file_name}"'})


@router.get("/projects/{project_id}/sheets/{sheet_id}/pages/{page_no}/image")
def page_image(project_id: uuid.UUID, sheet_id: uuid.UUID, page_no: int, user: CurrentUser, s: DB, scale: float = 2.0):
    project_access(s, user, project_id)
    sheet = _sheet(s, project_id, sheet_id)
    page = _guard(tk.get_page, s, sheet, page_no)
    return Response(tk.render_page(sheet, page, scale), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/projects/{project_id}/sheets/{sheet_id}/pages/{page_no}/snaps")
def page_snaps(project_id: uuid.UUID, sheet_id: uuid.UUID, page_no: int, user: CurrentUser, s: DB):
    project_access(s, user, project_id)
    sheet = _sheet(s, project_id, sheet_id)
    return tk.snaps(sheet, _guard(tk.get_page, s, sheet, page_no))


class PageIn(BaseModel):
    label: str


@router.patch("/projects/{project_id}/sheets/{sheet_id}/pages/{page_no}")
def update_page(project_id: uuid.UUID, sheet_id: uuid.UUID, page_no: int, body: PageIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    sheet = _sheet(s, project_id, sheet_id)
    return jsonable(tk.page_dict(_guard(tk.set_label, s, sheet, page_no, body.label, user.email)))


class ScaleIn(BaseModel):
    method: str
    paper: str | None = None
    real: str | None = None
    p1: list[float] | None = None
    p2: list[float] | None = None
    length: str | None = None


@router.put("/projects/{project_id}/sheets/{sheet_id}/pages/{page_no}/scale")
def set_scale(project_id: uuid.UUID, sheet_id: uuid.UUID, page_no: int, body: ScaleIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    sheet = _sheet(s, project_id, sheet_id)
    _guard(tk.set_scale, s, sheet, page_no, body.model_dump(exclude_none=True), user.email)
    return jsonable(tk.sheet_dict(s, sheet, detail=True))


class MarkIn(BaseModel):
    page_no: int
    kind: str
    subject: str
    label: str = ""
    points: list[list[float]]


@router.post("/projects/{project_id}/sheets/{sheet_id}/marks", status_code=201)
def add_mark(project_id: uuid.UUID, sheet_id: uuid.UUID, body: MarkIn, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    sheet = _sheet(s, project_id, sheet_id)
    mk = _guard(tk.add_mark, s, sheet, body.model_dump(), user.email)
    return jsonable(tk.mark_dict(mk, tk.get_page(s, sheet, mk.page_no)))


class MarkPatch(BaseModel):
    subject: str | None = None
    label: str | None = None
    points: list[list[float]] | None = Field(default=None)


@router.patch("/projects/{project_id}/sheets/{sheet_id}/marks/{mark_id}")
def update_mark(project_id: uuid.UUID, sheet_id: uuid.UUID, mark_id: uuid.UUID, body: MarkPatch, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    sheet = _sheet(s, project_id, sheet_id)
    mk = _guard(tk.update_mark, s, sheet, mark_id, body.model_dump(exclude_none=True), user.email)
    return jsonable(tk.mark_dict(mk, tk.get_page(s, sheet, mk.page_no)))


@router.delete("/projects/{project_id}/sheets/{sheet_id}/marks/{mark_id}", status_code=204)
def delete_mark(project_id: uuid.UUID, sheet_id: uuid.UUID, mark_id: uuid.UUID, user: CurrentUser, s: DB):
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _guard(tk.delete_mark, s, _sheet(s, project_id, sheet_id), mark_id, user.email)


class SendIn(BaseModel):
    version_id: uuid.UUID
    building_id: uuid.UUID | None = None


@router.post("/projects/{project_id}/sheets/{sheet_id}/send", status_code=201)
def send(project_id: uuid.UUID, sheet_id: uuid.UUID, body: SendIn, user: CurrentUser, s: DB):
    """Stage every mark of the drawing as an import batch on a draft estimate, for review and commit."""
    project_access(s, user, project_id, Perm.EDIT_ESTIMATE)
    _version(s, project_id, body.version_id)
    sheet = _sheet(s, project_id, sheet_id)
    b = _guard_import(tk.send_to_estimate, s, sheet, body.version_id, user.email, body.building_id)
    return _batch_out(s, b)
