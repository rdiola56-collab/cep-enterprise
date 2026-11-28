"""Project work breakdown: buildings, floors (incl. basements), zones and rooms."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m


def _owned(s: Session, model, row_id: uuid.UUID, project_id: uuid.UUID):
    row = s.get(model, row_id)
    if row is None or row.project_id != project_id:
        raise LookupError(f"{model.__tablename__[:-1]} not found in this project")
    return row


def add_building(s: Session, project_id: uuid.UUID, code: str, name: str) -> m.Building:
    b = m.Building(project_id=project_id, code=code, name=name)
    s.add(b)
    s.flush()
    return b


def add_floor(s: Session, project_id: uuid.UUID, building_id: uuid.UUID, code: str, name: str, level: int,
              is_basement: bool = False, gross_floor_area_m2: Decimal | None = None) -> m.Floor:
    _owned(s, m.Building, building_id, project_id)
    f = m.Floor(project_id=project_id, building_id=building_id, code=code, name=name, level=level,
                is_basement=is_basement, gross_floor_area_m2=gross_floor_area_m2)
    s.add(f)
    s.flush()
    return f


def add_zone(s: Session, project_id: uuid.UUID, floor_id: uuid.UUID, code: str, name: str) -> m.Zone:
    _owned(s, m.Floor, floor_id, project_id)
    z = m.Zone(project_id=project_id, floor_id=floor_id, code=code, name=name)
    s.add(z)
    s.flush()
    return z


def add_room(s: Session, project_id: uuid.UUID, floor_id: uuid.UUID, code: str, name: str, zone_id: uuid.UUID | None = None) -> m.Room:
    _owned(s, m.Floor, floor_id, project_id)
    if zone_id:
        _owned(s, m.Zone, zone_id, project_id)
    r = m.Room(project_id=project_id, floor_id=floor_id, zone_id=zone_id, code=code, name=name)
    s.add(r)
    s.flush()
    return r


def wbs(s: Session, project_id: uuid.UUID) -> list[dict]:
    out = []
    for b in s.scalars(select(m.Building).where(m.Building.project_id == project_id).order_by(m.Building.code)):
        floors = []
        for f in s.scalars(select(m.Floor).where(m.Floor.building_id == b.id).order_by(m.Floor.level)):
            zones = [{"id": str(z.id), "code": z.code, "name": z.name}
                     for z in s.scalars(select(m.Zone).where(m.Zone.floor_id == f.id).order_by(m.Zone.code))]
            rooms = [{"id": str(r.id), "code": r.code, "name": r.name, "zone_id": str(r.zone_id) if r.zone_id else None}
                     for r in s.scalars(select(m.Room).where(m.Room.floor_id == f.id).order_by(m.Room.code))]
            floors.append({"id": str(f.id), "code": f.code, "name": f.name, "level": f.level, "is_basement": f.is_basement,
                           "gross_floor_area_m2": format(f.gross_floor_area_m2.normalize(), "f") if f.gross_floor_area_m2 is not None else None,
                           "zones": zones, "rooms": rooms})
        out.append({"id": str(b.id), "code": b.code, "name": b.name, "floors": floors})
    return out


# ---------------------------------------------------------------- editing

_MODELS = {"building": m.Building, "floor": m.Floor, "zone": m.Zone, "room": m.Room}
_EDITABLE = {
    "building": ("code", "name"),
    "floor": ("code", "name", "level", "is_basement", "gross_floor_area_m2"),
    "zone": ("code", "name"),
    "room": ("code", "name", "zone_id"),
}


def update_node(s: Session, project_id: uuid.UUID, kind: str, node_id: uuid.UUID, changes: dict, actor: str = "system"):
    """Rename or re-describe a WBS node. Items refer to nodes by id, so their amounts never change;
    reports simply show the new name. A node cannot move to another parent (items record the full path)."""
    from .audit import audit

    if kind not in _MODELS:
        raise ValueError(f"unknown WBS level '{kind}'")
    row = _owned(s, _MODELS[kind], node_id, project_id)
    unknown = set(changes) - set(_EDITABLE[kind])
    if unknown:
        raise ValueError(f"cannot change {', '.join(sorted(unknown))} of a {kind}")
    if kind == "room" and changes.get("zone_id"):
        zone = _owned(s, m.Zone, uuid.UUID(str(changes["zone_id"])), project_id)
        if zone.floor_id != row.floor_id:
            raise ValueError("the zone is on another floor")
    for key in ("code", "name"):
        if key in changes and not str(changes[key]).strip():
            raise ValueError(f"{key} cannot be empty")
    before = {k: _plain(getattr(row, k)) for k in changes}
    for k, v in changes.items():
        setattr(row, k, v)
    s.flush()
    audit(s, project_id, _MODELS[kind].__tablename__, row.id, "update", actor,
          {"before": before, "after": {k: _plain(getattr(row, k)) for k in changes}})
    return row


def delete_node(s: Session, project_id: uuid.UUID, kind: str, node_id: uuid.UUID, actor: str = "system") -> None:
    """Delete an empty WBS node. Anything still referring to it (child nodes, estimate items in any
    version, staged import rows) blocks the delete with a count, so no estimate line loses its location."""
    from .audit import audit

    if kind not in _MODELS:
        raise ValueError(f"unknown WBS level '{kind}'")
    row = _owned(s, _MODELS[kind], node_id, project_id)
    col = {"building": m.EstimateItemRecord.building_id, "floor": m.EstimateItemRecord.floor_id,
           "zone": m.EstimateItemRecord.zone_id, "room": m.EstimateItemRecord.room_id}[kind]
    blockers = []
    n_items = s.scalar(select(func.count()).select_from(m.EstimateItemRecord).where(col == row.id))
    if n_items:
        blockers.append(f"{n_items} estimate item(s)")
    children = {"building": [(m.Floor, m.Floor.building_id, "floor")], "floor": [(m.Zone, m.Zone.floor_id, "zone"), (m.Room, m.Room.floor_id, "room")],
                "zone": [(m.Room, m.Room.zone_id, "room")], "room": []}[kind]
    for model, fk, label in children:
        n = s.scalar(select(func.count()).select_from(model).where(fk == row.id))
        if n:
            blockers.append(f"{n} {label}(s)")
    if kind == "floor":
        n = s.scalar(select(func.count()).select_from(m.ImportRow).where(m.ImportRow.floor_id == row.id))
        if n:
            blockers.append(f"{n} import row(s)")
    if blockers:
        raise NodeInUse(f"{kind} {row.code} is still used by {', '.join(blockers)}")
    s.delete(row)
    s.flush()
    audit(s, project_id, _MODELS[kind].__tablename__, node_id, "delete", actor, {"code": row.code, "name": row.name})


class NodeInUse(ValueError):
    pass


def _plain(v):
    return str(v) if isinstance(v, (Decimal, uuid.UUID)) else v
