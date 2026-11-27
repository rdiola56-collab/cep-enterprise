"""Users, authentication and project membership."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.permissions import GLOBAL_ROLES, PROJECT_ROLES
from ..auth.security import hash_password, verify_password
from ..db.models import ProjectMember, User
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


def authenticate(s: Session, email: str, password: str) -> User | None:
    u = s.scalars(select(User).where(User.email == email.strip().lower())).first()
    if u is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not u.is_active or not verify_password(password, u.password_hash):
        return None
    return u


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
