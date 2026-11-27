from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..db.models import AuditLog


def audit(s: Session, project_id: uuid.UUID | None, entity: str, entity_id, action: str, actor: str, data: dict | None = None) -> None:
    s.add(AuditLog(project_id=project_id, entity=entity, entity_id=str(entity_id), action=action, actor=actor, data=data or {}))
