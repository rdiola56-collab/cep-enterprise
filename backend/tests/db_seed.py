"""Load the test fixtures into PostgreSQL through the real library services."""
from __future__ import annotations

from app.services import library as lib
from app.services.seed import seed_standard_formulas

from . import fixtures as fx


def seed_master(s) -> None:
    seed_standard_formulas(s)
    p = fx.PARAMS
    lib.set_cost_parameters(
        s, p.currency, p.hours_per_day,
        [{"code": mk.code, "label": mk.label, "pct": mk.pct, "basis": mk.basis} for mk in p.markups],
        p.productivity_factors,
    )
    for pr in fx.BASE_PRICES:
        lib.add_material(s, pr.item_code, pr.item_code, pr.unit, pr.category)
        lib.add_material_price(s, pr.item_code, pr.unit, pr.price, pr.currency, pr.valid_from)
    for lr in fx.LABOR_RATES:
        lib.add_labor_rate(s, lr.labor_class, lr.hourly_rate, lr.currency, lr.valid_from)
    for c in fx.CREWS:
        lib.add_crew(s, c.code, c.name, [(mm.labor_class, mm.count) for mm in c.members])
    for pr in fx.PRODUCTIVITIES:
        lib.add_productivity(s, pr.code, pr.output_per_crew_day, pr.unit, pr.condition)
    for e in fx.EQUIPMENT:
        lib.add_equipment_rate(s, e.equipment_code, e.rental_per_hour, e.currency, e.valid_from, e.fuel_litres_per_hour,
                               e.fuel_item_code, e.operating_per_hour)
    for a in fx.ASSEMBLIES.values():
        lib.save_assembly(s, a)
