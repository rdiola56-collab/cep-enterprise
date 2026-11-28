"""What CEP tells people by email. Every function is a no-op when email is not set up.

Messages are plain text, name what happened and who did it, and never contain passwords
or prices beyond what the recipient can already see in CEP.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from . import mail

SIGN_OFF = "\n\n-- \nCEP: ENTERPRISE (automatic message; replies are not read)\n"


def reset_link(s: Session, user: m.User, token: str, expires: datetime, requested_by: str) -> m.EmailMessage | None:
    by_self = requested_by == user.email
    why = "You asked to reset your CEP password." if by_self else f"{requested_by} sent you a link to set your CEP password."
    body = (f"Hello {user.name},\n\n{why}\n\nOpen this link to choose a new password:\n"
            f"{mail.link('/reset-password?token=' + token)}\n\n"
            f"The link works once and expires at {expires:%Y-%m-%d %H:%M} UTC. Using it signs you out everywhere else.\n"
            + ("If you did not ask for this, ignore this message: your password stays as it is." if by_self else
               "If you were not expecting this, tell your CEP administrator."))
    return mail.queue(s, to=user.email, subject="Reset your CEP password", body=body + SIGN_OFF, kind="reset_link",
                      user_id=user.id, sensitive=True)


def password_changed(s: Session, user: m.User, how: str) -> m.EmailMessage | None:
    body = (f"Hello {user.name},\n\nThe password of your CEP account was {how}. Every other session was signed out.\n\n"
            "If this was not you, contact your CEP administrator straight away.")
    return mail.queue(s, to=user.email, subject="Your CEP password was changed", body=body + SIGN_OFF,
                      kind="password_changed", user_id=user.id)


def account_locked(s: Session, user: m.User, minutes: int) -> m.EmailMessage | None:
    body = (f"Hello {user.name},\n\nYour CEP account was locked for {minutes} minutes after several wrong passwords in a row.\n\n"
            "If that was you, wait and try again, or ask your administrator to unlock it. If it was not you, someone may be "
            "trying to guess your password: tell your administrator.")
    return mail.queue(s, to=user.email, subject="Your CEP account was locked", body=body + SIGN_OFF,
                      kind="account_locked", user_id=user.id)


def project_added(s: Session, user: m.User, project: m.Project, role: str, actor: str) -> m.EmailMessage | None:
    body = (f"Hello {user.name},\n\n{actor} gave you the {role} role on project {project.code} ({project.name}).\n\n"
            f"Open it: {mail.link('/projects/' + str(project.id))}")
    return mail.queue(s, to=user.email, subject=f"CEP: you were added to {project.code}", body=body + SIGN_OFF,
                      kind="project_added", user_id=user.id, project_id=project.id)


def estimate_approved(s: Session, version: m.EstimateVersion, actor: str) -> list[m.EmailMessage]:
    """Tell the project's managers and estimators (not the approver) that a version was approved."""
    if not mail.enabled():
        return []
    project = s.get(m.Project, version.project_id)
    estimate = s.get(m.EstimateRecord, version.estimate_id)
    people = s.execute(select(m.User).join(m.ProjectMember, m.ProjectMember.user_id == m.User.id)
                       .where(m.ProjectMember.project_id == version.project_id, m.ProjectMember.role.in_(("manager", "estimator")),
                              m.User.is_active.is_(True), m.User.email != actor)).scalars().all()
    out = []
    for u in people:
        body = (f"Hello {u.name},\n\n{actor} approved {estimate.code} {estimate.name}, version {version.version_no}, on project "
                f"{project.code}. It is now frozen; changes need a new revision.\n\n"
                f"Total: {project.currency} {version.total_amount:,.2f}\n\n"
                f"Open it: {mail.link(f'/projects/{project.id}/versions/{version.id}')}")
        msg = mail.queue(s, to=u.email, subject=f"CEP: {estimate.code} v{version.version_no} approved", body=body + SIGN_OFF,
                         kind="estimate_approved", user_id=u.id, project_id=project.id)
        if msg:
            out.append(msg)
    return out


def test_message(s: Session, user: m.User) -> m.EmailMessage | None:
    body = f"Hello {user.name},\n\nThis is a test message from CEP. Email is working."
    return mail.queue(s, to=user.email, subject="CEP test message", body=body + SIGN_OFF, kind="test", user_id=user.id)
