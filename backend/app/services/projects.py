"""Project work breakdown: buildings, floors (incl. basements), zones and rooms."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
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
                           "gross_floor_area_m2": str(f.gross_floor_area_m2) if f.gross_floor_area_m2 is not None else None,
                           "zones": zones, "rooms": rooms})
        out.append({"id": str(b.id), "code": b.code, "name": b.name, "floors": floors})
    return out
