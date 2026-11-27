"""Seed the master library with the reference formulas from the specification.

Only formula *methodology* is seeded. Prices, wages, productivity and
equipment rates are never seeded or hard-coded: they are entered per master
library or per project through the API.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Formula
from ..engine.formula.library import standard_formulas
from .library import publish_formula


def seed_standard_formulas(s: Session) -> int:
    existing = set(s.scalars(select(Formula.code).where(Formula.project_id.is_(None))))
    n = 0
    for f in standard_formulas():
        if f.code not in existing:
            publish_formula(s, f)
            n += 1
    return n


if __name__ == "__main__":
    from ..db.session import session_scope

    with session_scope() as s:
        print(f"seeded {seed_standard_formulas(s)} formulas")
