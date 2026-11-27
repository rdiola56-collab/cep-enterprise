"""Authentication and authorisation dependencies for the API."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..auth.permissions import Perm, allowed
from ..auth.security import AuthError, read_token
from ..db.models import Project, User
from ..db.session import get_session
from ..services.users import project_role

DB = Annotated[Session, Depends(get_session)]


def current_user(s: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "not signed in", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = read_token(authorization.split(" ", 1)[1].strip())
        user = s.get(User, uuid.UUID(claims["sub"]))
    except (AuthError, KeyError, ValueError) as exc:
        raise HTTPException(401, str(exc) or "invalid token", headers={"WWW-Authenticate": "Bearer"}) from None
    if user is None or not user.is_active or user.token_version != claims.get("ver"):
        raise HTTPException(401, "session is no longer valid", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass
class Access:
    user: User
    project: Project | None
    project_role: str | None

    def require(self, perm: Perm) -> None:
        if not allowed(self.user.global_role, perm, self.project_role):
            raise HTTPException(403, f"you do not have permission to {perm.value.replace('_', ' ')}")


def require_global(user: User, perm: Perm) -> None:
    if not allowed(user.global_role, perm):
        raise HTTPException(403, f"you do not have permission to {perm.value.replace('_', ' ')}")


def project_access(s: Session, user: User, project_id: uuid.UUID, perm: Perm = Perm.READ_PROJECT) -> Access:
    """Resolve a project for a user. Non-members get 404, so other projects' existence is not revealed."""
    project = s.get(Project, project_id)
    role = project_role(s, project_id, user.id) if project else None
    if project is None or not allowed(user.global_role, Perm.READ_PROJECT, role):
        raise HTTPException(404, "project not found")
    access = Access(user, project, role)
    access.require(perm)
    return access


def library_scope(s: Session, user: User, project_id: uuid.UUID | None) -> None:
    """Writing master-library records needs a library role; project records need project-library rights."""
    if project_id is None:
        require_global(user, Perm.EDIT_MASTER_LIBRARY)
    else:
        project_access(s, user, project_id, Perm.EDIT_PROJECT_LIBRARY)
