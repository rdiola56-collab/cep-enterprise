"""Role-based permissions.

Global roles (on the user):
  admin          everything, including users and every project
  cost_engineer  maintains the master library (formulas, assemblies, prices, rates); can create projects
  estimator      can create projects (becoming their manager)
  viewer         no global rights; sees only projects they are a member of

Project roles (per project membership):
  manager   edit estimates, approve, maintain the project's own library records, manage members
  estimator edit estimates (calculate items, create estimates and revisions)
  approver  approve estimate versions (read-only otherwise)
  viewer    read only
"""
from __future__ import annotations

from enum import Enum

GLOBAL_ROLES = ("admin", "cost_engineer", "estimator", "viewer")
PROJECT_ROLES = ("manager", "estimator", "approver", "viewer")


class Perm(str, Enum):
    MANAGE_USERS = "manage_users"
    CREATE_PROJECT = "create_project"
    EDIT_MASTER_LIBRARY = "edit_master_library"
    READ_PROJECT = "read_project"
    EDIT_ESTIMATE = "edit_estimate"
    APPROVE_ESTIMATE = "approve_estimate"
    EDIT_PROJECT_LIBRARY = "edit_project_library"
    MANAGE_MEMBERS = "manage_members"


_GLOBAL: dict[str, set[Perm]] = {
    "admin": set(Perm),
    "cost_engineer": {Perm.CREATE_PROJECT, Perm.EDIT_MASTER_LIBRARY},
    "estimator": {Perm.CREATE_PROJECT},
    "viewer": set(),
}

_PROJECT: dict[str, set[Perm]] = {
    "manager": {Perm.READ_PROJECT, Perm.EDIT_ESTIMATE, Perm.APPROVE_ESTIMATE, Perm.EDIT_PROJECT_LIBRARY, Perm.MANAGE_MEMBERS},
    "estimator": {Perm.READ_PROJECT, Perm.EDIT_ESTIMATE},
    "approver": {Perm.READ_PROJECT, Perm.APPROVE_ESTIMATE},
    "viewer": {Perm.READ_PROJECT},
}


def allowed(global_role: str, perm: Perm, project_role: str | None = None) -> bool:
    if perm in _GLOBAL.get(global_role, set()):
        return True
    return project_role is not None and perm in _PROJECT.get(project_role, set())
