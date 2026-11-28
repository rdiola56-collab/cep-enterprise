"""Outgoing email through the company's SMTP server.

Off until configured; nothing pretends to send mail when it is not set up:

    CEP_SMTP_HOST        e.g. smtp.office365.com, smtp.gmail.com, an internal relay
    CEP_SMTP_PORT        default 587 (465 with CEP_SMTP_SECURITY=ssl)
    CEP_SMTP_SECURITY    starttls (default) | ssl | none (only for a relay on a trusted network)
    CEP_SMTP_USER, CEP_SMTP_PASSWORD   optional login
    CEP_SMTP_CA_FILE     optional CA bundle for an internal server's certificate
    CEP_MAIL_FROM        sender address, e.g. "CEP Estimating <cep@example.com>"
    CEP_APP_URL          the web app's address, for links in messages (https://cep.example.com)

Messages go into an outbox table first. With a background worker (CEP_REDIS_URL) they are
sent after the transaction that created them commits, with retries; without one they are
sent straight away inside the request. Messages holding a secret link (password resets) are
not shown in the outbox list and their body is blanked once sent.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..db import models as m

MAX_ATTEMPTS = 5
SENT_PLACEHOLDER = "[message body removed after sending: it contained a one-time link]"


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    user: str | None
    password: str | None
    sender: str
    app_url: str
    ca_file: str | None
    timeout: int = 30


def config() -> MailConfig | None:
    host, sender, app_url = (os.environ.get(k) for k in ("CEP_SMTP_HOST", "CEP_MAIL_FROM", "CEP_APP_URL"))
    if not (host and sender and app_url):
        return None
    security = (os.environ.get("CEP_SMTP_SECURITY") or "starttls").lower()
    if security not in ("starttls", "ssl", "none"):
        security = "starttls"
    port = int(os.environ.get("CEP_SMTP_PORT") or (465 if security == "ssl" else 587))
    return MailConfig(host, port, security, os.environ.get("CEP_SMTP_USER") or None, os.environ.get("CEP_SMTP_PASSWORD") or None,
                      sender, app_url.rstrip("/"), os.environ.get("CEP_SMTP_CA_FILE") or None)


def enabled() -> bool:
    return config() is not None


def status() -> dict:
    cfg = config()
    if cfg is None:
        return {"enabled": False, "detail": "not set up: set CEP_SMTP_HOST, CEP_MAIL_FROM and CEP_APP_URL"}
    return {"enabled": True, "server": f"{cfg.host}:{cfg.port}", "security": cfg.security, "sender": cfg.sender}


def link(path: str) -> str:
    cfg = config()
    return f"{cfg.app_url}{path}" if cfg else path


# ------------------------------------------------------------------ outbox


def queue(s: Session, *, to: str, subject: str, body: str, kind: str, user_id: uuid.UUID | None = None,
          project_id: uuid.UUID | None = None, sensitive: bool = False) -> m.EmailMessage | None:
    """Put a message in the outbox and send it (now, or after commit with a worker). None if mail is off."""
    if not enabled():
        return None
    msg = m.EmailMessage(kind=kind, to_addr=to, subject=subject[:300], body=body, sensitive=sensitive, user_id=user_id,
                         project_id=project_id, status="queued", attempts=0)
    s.add(msg)
    s.flush()
    from .. import jobs

    if jobs.MODE == "celery":
        s.info.setdefault("cep_mail_pending", []).append(str(msg.id))  # sent once this transaction commits
    else:
        deliver(s, msg)
    return msg


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop("cep_mail_pending", None)
    if pending:
        from .. import jobs

        for mid in pending:
            jobs.send_email_task.delay(mid)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("cep_mail_pending", None)  # the messages were never stored


def deliver(s: Session, msg: m.EmailMessage) -> bool:
    """Try to send one message now. Records the outcome on the message; never raises for SMTP problems."""
    cfg = config()
    if msg.status == "sent":
        return True
    msg.attempts += 1
    if cfg is None:
        msg.status, msg.last_error = "failed", "email is not set up on this server"
        s.flush()
        return False
    try:
        send_smtp(cfg, msg.to_addr, msg.subject, msg.body)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        msg.status, msg.last_error = "failed", f"{exc.__class__.__name__}: {exc}"[:2000]
        s.flush()
        return False
    msg.status, msg.sent_at, msg.last_error = "sent", datetime.now(timezone.utc), None
    if msg.sensitive:
        msg.body = SENT_PLACEHOLDER
    s.flush()
    return True


def send_smtp(cfg: MailConfig, to: str, subject: str, body: str) -> None:
    mime = MimeMessage()
    mime["From"] = cfg.sender
    mime["To"] = to
    mime["Subject"] = subject
    mime["Message-ID"] = make_msgid(domain=cfg.sender.rsplit("@", 1)[-1].strip(">") or None)
    mime["Auto-Submitted"] = "auto-generated"
    mime.set_content(body)
    context = ssl.create_default_context(cafile=cfg.ca_file)
    if cfg.security == "ssl":
        smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=context)
    else:
        smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
    with smtp:
        smtp.ehlo()
        if cfg.security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if cfg.user:
            smtp.login(cfg.user, cfg.password or "")
        smtp.send_message(mime)


def retry(s: Session, message_id: uuid.UUID) -> m.EmailMessage:
    msg = s.get(m.EmailMessage, message_id)
    if msg is None:
        raise LookupError("message not found")
    if msg.status == "sent":
        raise ValueError("this message was already sent")
    deliver(s, msg)
    return msg


def outbox(s: Session, limit: int = 50) -> list[m.EmailMessage]:
    return list(s.scalars(select(m.EmailMessage).order_by(m.EmailMessage.created_at.desc()).limit(min(limit, 200))))


def message_dict(msg: m.EmailMessage) -> dict:
    return {"id": str(msg.id), "kind": msg.kind, "to": msg.to_addr, "subject": msg.subject, "status": msg.status,
            "attempts": msg.attempts, "last_error": msg.last_error,
            "body": None if msg.sensitive else msg.body,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None}
