"""Users, authentication and project membership."""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.permissions import GLOBAL_ROLES, PROJECT_ROLES
from ..auth.security import hash_password, verify_password
from ..db.models import PasswordReset, ProjectMember, User
from .audit import audit

# Burned on unknown emails so response time does not reveal which accounts exist.
_DUMMY_HASH = hash_password("not-a-real-password-0")


def create_user(s: Session, email: str, name: str, password: str, global_role: str = "viewer", actor: str = "system") -> User:
    if global_role not in GLOBAL_ROLES:
        raise ValueError(f"unknown role '{global_role}'")
    email = email.strip().lower()
    if s.scalars(select(User).where(User.email == email)).first():
        raise ValueError("a user with this email already exists")
    u = User(email=email, name=name, password_hash=hash_password(password), global_role=global_role)
    s.add(u)
    s.flush()
    audit(s, None, "users", u.id, "create", actor, {"email": email, "role": global_role})
    return u


MAX_FAILED_LOGINS = int(os.environ.get("CEP_MAX_FAILED_LOGINS", 5))
LOCKOUT = timedelta(minutes=int(os.environ.get("CEP_LOCKOUT_MINUTES", 15)))
RESET_TTL = timedelta(hours=int(os.environ.get("CEP_RESET_LINK_HOURS", 24)))
SELF_RESET_TTL = timedelta(minutes=int(os.environ.get("CEP_SELF_RESET_MINUTES", 60)))
SELF_RESETS_PER_HOUR = 3


class AccountLocked(PermissionError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def authenticate(s: Session, email: str, password: str) -> User | None:
    """Check a password. After MAX_FAILED_LOGINS wrong passwords in a row the account is locked for
    LOCKOUT, even for the right password; an administrator can unlock it or issue a reset link."""
    u = s.scalars(select(User).where(User.email == email.strip().lower())).first()
    if u is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if u.locked_until and u.locked_until > _now():
        verify_password(password, _DUMMY_HASH)
        raise AccountLocked("too many failed sign-in attempts; try again later or ask an administrator")
    if not u.is_active or not verify_password(password, u.password_hash):
        u.failed_logins = (u.failed_logins or 0) + 1
        if u.failed_logins >= MAX_FAILED_LOGINS:
            u.locked_until, u.failed_logins = _now() + LOCKOUT, 0
            audit(s, None, "users", u.id, "locked", "system", {"minutes": LOCKOUT.total_seconds() / 60})
            from . import notify

            notify.account_locked(s, u, int(LOCKOUT.total_seconds() // 60))
        s.flush()
        return None
    u.failed_logins, u.locked_until = 0, None
    s.flush()
    return u


def change_password(s: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise ValueError("current password is incorrect")
    user.password_hash = hash_password(new)
    user.token_version += 1  # other sessions end; the caller issues a fresh token
    audit(s, None, "users", user.id, "change_password", user.email, {})
    from . import notify

    notify.password_changed(s, user, "changed from your account page")


def issue_reset_link(s: Session, user_id: uuid.UUID, actor: str, ttl: timedelta = RESET_TTL) -> tuple[str, datetime]:
    """A one-time token. Emailed to the user when email is set up; the administrator also gets it to pass on."""
    u = s.get(User, user_id)
    if u is None:
        raise LookupError("user not found")
    token = secrets.token_urlsafe(32)
    expires = _now() + ttl
    s.add(PasswordReset(user_id=u.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires, created_by=actor))
    s.flush()
    from . import notify

    emailed = notify.reset_link(s, u, token, expires, actor) is not None
    audit(s, None, "users", u.id, "reset_link", actor, {"expires_at": expires.isoformat(), "emailed": emailed})
    return token, expires


def request_reset(s: Session, email: str) -> None:
    """'Forgot password': email a short-lived link if the address belongs to an active account.
    Says nothing either way, so the form cannot be used to find out who has an account."""
    from . import mail

    if not mail.enabled():
        raise ValueError("password reset by email is not set up; ask your administrator for a reset link")
    u = s.scalars(select(User).where(User.email == email.strip().lower())).first()
    if u is None or not u.is_active:
        return
    recent = s.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id,
                                                   PasswordReset.created_at > _now() - timedelta(hours=1))).all()
    if len(recent) >= SELF_RESETS_PER_HOUR:
        audit(s, None, "users", u.id, "reset_request_throttled", u.email, {})
        return
    issue_reset_link(s, u.id, u.email, SELF_RESET_TTL)


def reset_password(s: Session, token: str, new: str) -> User:
    r = s.scalars(select(PasswordReset).where(PasswordReset.token_hash == hashlib.sha256(token.encode()).hexdigest())).first()
    if r is None or r.used_at is not None or r.expires_at < _now():
        raise ValueError("this reset link is invalid, used or expired; ask for a new one")
    u = s.get(User, r.user_id)
    u.password_hash = hash_password(new)  # validates length before anything is marked used
    r.used_at = _now()
    u.failed_logins, u.locked_until = 0, None
    u.token_version += 1
    s.flush()
    audit(s, None, "users", u.id, "reset_password", u.email, {})
    from . import notify

    notify.password_changed(s, u, "reset with a one-time link")
    return u


def unlock(s: Session, user_id: uuid.UUID, actor: str) -> None:
    u = s.get(User, user_id)
    if u is None:
        raise LookupError("user not found")
    u.failed_logins, u.locked_until = 0, None
    audit(s, None, "users", u.id, "unlock", actor, {})


def set_active(s: Session, user_id: uuid.UUID, active: bool, actor: str) -> None:
    u = s.get(User, user_id)
    if u is None:
        raise LookupError("user not found")
    u.is_active = active
    if not active:
        u.token_version += 1
    audit(s, None, "users", u.id, "activate" if active else "deactivate", actor, {})


def set_member(s: Session, project_id: uuid.UUID, user_id: uuid.UUID, role: str, actor: str = "system") -> ProjectMember:
    if role not in PROJECT_ROLES:
        raise ValueError(f"unknown project role '{role}'")
    m = s.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)).first()
    if m is None:
        m = ProjectMember(project_id=project_id, user_id=user_id, role=role)
        s.add(m)
    else:
        m.role = role
    s.flush()
    audit(s, project_id, "project_members", user_id, "set_role", actor, {"role": role})
    return m


def project_role(s: Session, project_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    return s.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))


if __name__ == "__main__":  # python -m app.services.users create-admin <email> <name>
    import getpass
    import sys

    from ..db.session import session_scope

    if len(sys.argv) != 4 or sys.argv[1] != "create-admin":
        sys.exit("usage: python -m app.services.users create-admin <email> <name>")
    pw = getpass.getpass("Password (min 10 characters): ")
    with session_scope() as s:
        u = create_user(s, sys.argv[2], sys.argv[3], pw, "admin")
        print(f"created admin {u.email}")
