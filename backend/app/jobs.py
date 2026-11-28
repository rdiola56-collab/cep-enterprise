"""Background jobs: large imports and report exports.

Two modes, chosen by CEP_JOBS_MODE (default: "celery" when CEP_REDIS_URL is set, else "inline"):

    celery   the API stores the job and queues its id on Redis; a Celery worker
             (`celery -A app.jobs worker`) runs it and writes status and result back to PostgreSQL.
    inline   no worker: the job runs inside the request that created it. Same code path and
             result, but a big file holds the request until it is done.

Jobs only call the ordinary services (imports.stage, reports.*), so a background import is
validated and priced exactly like one made in the request.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .db import models as m

REDIS_URL = os.environ.get("CEP_REDIS_URL")
MODE = os.environ.get("CEP_JOBS_MODE") or ("celery" if REDIS_URL else "inline")

celery_app = None
if MODE == "celery":
    from celery import Celery

    celery_app = Celery("cep", broker=REDIS_URL or "redis://localhost:6379/0")
    celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_serializer="json",
                           broker_connection_retry_on_startup=True)

    @celery_app.task(name="cep.run_job")
    def run_job_task(job_id: str) -> str:
        run_job(uuid.UUID(job_id))
        return job_id

app = celery_app  # `celery -A app.jobs worker` looks for `app`


def create(s: Session, project_id: uuid.UUID, kind: str, params: dict, actor: str, payload: bytes | None = None) -> m.Job:
    job = m.Job(project_id=project_id, kind=kind, params=params, payload=payload, status="queued", created_by=actor)
    s.add(job)
    s.flush()
    return job


def dispatch(s: Session, job: m.Job) -> m.Job:
    """Run (inline) or queue (celery) a job. In celery mode the caller's transaction is committed first,
    so the worker can see the job row."""
    if MODE == "celery":
        s.commit()
        run_job_task.delay(str(job.id))
        return job
    return run_job(job.id, s)


def run_job(job_id: uuid.UUID, s: Session | None = None) -> m.Job:
    if s is None:
        from .db.session import session_scope

        with session_scope() as own:
            return _run(own, job_id, commit_progress=True)
    return _run(s, job_id, commit_progress=False)


def _run(s: Session, job_id: uuid.UUID, commit_progress: bool) -> m.Job:
    job = s.get(m.Job, job_id)
    if job is None or job.status not in ("queued", "running"):
        return job
    job.status, job.started_at = "running", datetime.now(timezone.utc)
    if commit_progress:
        s.commit()
    try:
        with s.begin_nested():  # the job's work is all or nothing; its status row survives a failure
            _perform(s, job)
        job.status = "succeeded"
    except Exception as exc:  # recorded for the user; nothing half-done is kept
        job.status, job.error = "failed", _message(exc)
    job.finished_at = datetime.now(timezone.utc)
    s.flush()
    return job


def _message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or exc.__class__.__name__


def _perform(s: Session, job: m.Job) -> None:
    p = job.params
    if job.kind == "export":
        from .services import reports as rpt

        version_id = uuid.UUID(p["version_id"])
        v = s.get(m.EstimateVersion, version_id)
        if v is None or v.project_id != job.project_id:
            raise LookupError("estimate version not found")
        data = rpt.estimate_report_data(s, version_id)
        if p["format"] == "xlsx":
            body, media = rpt.to_excel(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif p["format"] == "pdf":
            body, media = rpt.to_pdf(data), "application/pdf"
        else:
            raise ValueError(f"unknown export format '{p['format']}'")
        rpt.record_report(s, version_id, f"estimate.{p['format']}", data, job.created_by)
        project = s.get(m.Project, job.project_id)
        job.output, job.output_type = body, media
        job.output_name = f"{project.code}_estimate_v{v.version_no}_{v.status}.{p['format']}".replace(" ", "_")
        job.result = {"bytes": len(body)}
    elif job.kind == "import_stage":
        from .services import imports as imp

        batch = imp.stage(s, uuid.UUID(p["version_id"]), adapter=p["adapter"], file_name=p["file_name"], content=job.payload,
                          source_name=p["source_name"], source_revision=p["source_revision"], options=p.get("options") or {},
                          building_id=uuid.UUID(p["building_id"]) if p.get("building_id") else None,
                          actor=job.created_by, project_id=job.project_id)
        job.result = {"batch_id": str(batch.id), "counts": batch.summary.get("counts", {}), "items": batch.summary.get("items")}
        job.payload = None  # the batch keeps the file's hash; the bytes are not needed any more
    else:
        raise ValueError(f"unknown job kind '{job.kind}'")


def job_dict(job: m.Job) -> dict:
    return {"id": str(job.id), "kind": job.kind, "status": job.status, "params": job.params, "result": job.result,
            "error": job.error, "output_name": job.output_name, "created_by": job.created_by,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None, "mode": MODE}
