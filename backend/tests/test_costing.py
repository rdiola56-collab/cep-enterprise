"""Material, labor, equipment and rate-analysis engines, against hand calculations."""
from decimal import Decimal as Dec

import pytest

from app.engine.costing import CostingError, calculate_item
from app.engine.precision import money
from app.engine.rates import RateNotFound

from .fixtures import AS_OF, FORMWORK, NO_MARKUP, RC_BEAM, TILE_FLOOR, price, ratebook, BASE_PRICES


def test_labor_example_from_spec():
    """200 m², crew 1 foreman + 2 carpenters + 2 helpers, 20 m²/crew-day, 8 h/day."""
    b = calculate_item(FORMWORK, 200, ratebook(NO_MARKUP), AS_OF)
    (l,) = b.labor
    assert l.crew_days == 10
    assert l.crew_size == 5
    assert l.labor_hours == 400
    # 8h × 10 days × (1×100 + 2×80 + 2×60) = 30,400
    assert l.amount == 30400
    assert b.totals["labor"] == 30400
    assert b.unit_rate == 152
    by_class = {m.labor_class: m.hours for m in l.members}
    assert by_class == {"foreman": 80, "carpenter": 160, "helper": 160}


@pytest.mark.parametrize(
    "condition,crew_days,amount,source",
    [
        ("normal", Dec(10), Dec(30400), "prod:FORMWORK"),
        ("high_rise", Dec("12.5"), Dec(38000), "prod:FORMWORK×factor:high_rise"),  # factor 0.8 → 16 m²/day
        ("basement", Dec(200) / Dec(15), Dec(200) / Dec(15) * 8 * 380, "prod:FORMWORK:basement"),  # explicit record
    ],
)
def test_labor_productivity_conditions(condition, crew_days, amount, source):
    b = calculate_item(FORMWORK, 200, ratebook(), AS_OF, condition=condition)
    (l,) = b.labor
    assert money(l.crew_days, 10) == money(crew_days, 10)
    assert money(l.amount, 6) == money(amount, 6)
    assert l.productivity_source == source


def test_condition_without_record_or_factor_is_an_error_not_a_silent_default():
    # NO_MARKUP has no productivity factors, and TILING has no basement record.
    with pytest.raises(RateNotFound, match="factor"):
        calculate_item(TILE_FLOOR, 1, ratebook(NO_MARKUP), AS_OF, condition="basement")


def test_manpower_for_target_duration():
    b = calculate_item(FORMWORK, 200, ratebook(NO_MARKUP), AS_OF, target_days=Dec(4))
    (l,) = b.labor
    assert l.crews_required == 3  # 10 crew-days in 4 days
    assert l.manpower == 15


def test_productivity_in_other_unit_is_converted():
    """Productivity recorded as ft²/crew-day for an m² assembly."""
    from .fixtures import PRODUCTIVITIES
    from app.engine.rates import Productivity

    prods = [p for p in PRODUCTIVITIES if p.code != "FORMWORK"] + [
        Productivity("FORMWORK", "normal", Dec("215.2782"), "ft2", "prod:ft2")
    ]
    b = calculate_item(FORMWORK, 200, ratebook(NO_MARKUP, productivities=prods), AS_OF)
    assert money(b.labor[0].crew_days, 4) == Dec("10.0000")


def test_equipment_costing():
    b = calculate_item(RC_BEAM, 30, ratebook(NO_MARKUP), AS_OF)
    eq = {e.equipment_code: e for e in b.equipment}
    vib = eq["VIBRATOR"]  # runs for the placing task: 30 m³ / 30 m³ per day = 1 day = 8 h
    assert vib.hours == 8
    assert vib.rental_cost == 480
    assert vib.fuel_litres == Dec("6.4")
    assert vib.fuel_cost == Dec("416.00")  # 6.4 L × 65
    assert vib.operating_cost == 80
    assert vib.amount == Dec("976.00")
    crane = eq["TOWER-CRANE"]  # 0.1 h per m³
    assert crane.hours == 3
    assert crane.amount == 3 * 2500 + 3 * 300
    assert b.totals["equipment"] == Dec("976.00") + 8400


def test_material_wastage_and_price_unit_conversion():
    b = calculate_item(RC_BEAM, 10, ratebook(NO_MARKUP), AS_OF)
    m = {x.item_code: x for x in b.materials}
    rebar = m["REBAR-G60"]
    assert rebar.net_quantity == 1500  # kg
    assert rebar.gross_quantity == 1605  # +7 %
    assert rebar.priced_quantity == Dec("1.605")  # t
    assert rebar.amount == Dec("1.605") * 52000
    assert m["RMC-4000"].gross_quantity == Dec("10.5")


def test_incompatible_material_unit_is_rejected():
    prices = [p for p in BASE_PRICES if p.item_code != "REBAR-G60"] + [price("REBAR-G60", "m", "100")]
    with pytest.raises(CostingError, match="priced per m"):
        calculate_item(RC_BEAM, 1, ratebook(NO_MARKUP, prices=prices), AS_OF)


def test_rate_analysis_hand_calculation():
    """100 m² of floor tiling, every figure checked by hand (see docs/ENGINE.md)."""
    b = calculate_item(TILE_FLOOR, 100, ratebook(), AS_OF)
    t = b.totals
    assert t["material"] == Dec("33811.50")  # 291.9 tiles × 85 + 20 bags × 450
    assert money(t["labor"]) == Dec("10666.67")  # 66.67 h × (90 + 70)
    assert t["equipment"] == Dec("225")  # 5 h × (40 + 5)
    assert money(t["direct"]) == Dec("44703.17")
    assert money(t["overhead"]) == Dec("4470.32")
    assert money(t["contingency"]) == Dec("2235.16")
    assert money(t["profit"]) == Dec("4470.32")
    assert money(t["tax"]) == Dec("6705.48")  # 12 % of 55,878.96
    assert money(t["total"]) == Dec("62584.43")
    assert money(t["unit_rate"]) == Dec("625.84")
    assert money(t["direct_unit_cost"]) == Dec("447.03")
    # Totals reconcile exactly before rounding.
    parts = t["direct"] + t["overhead"] + t["contingency"] + t["profit"] + t["tax"]
    assert abs(parts - t["total"]) < Dec("1e-20")


def test_price_validity_dates_and_revisions():
    from datetime import date

    prices = [p for p in BASE_PRICES if p.item_code != "TILE-600"] + [
        price("TILE-600", "pcs", "80", valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30), source_id="old"),
        price("TILE-600", "pcs", "85", valid_from=date(2026, 7, 1), source_id="new"),
        price("TILE-600", "pcs", "88", valid_from=date(2026, 7, 1), revision=2, source_id="new-r2"),
        price("TILE-600", "pcs", "70", valid_from=date(2026, 7, 1), supplier="Acme", source_id="acme"),
    ]
    rb = ratebook(NO_MARKUP, prices=prices)
    assert rb.material_price("TILE-600", date(2026, 3, 1)).source_id == "old"
    assert rb.material_price("TILE-600", date(2026, 8, 1)).source_id == "new-r2"
    assert rb.material_price("TILE-600", date(2026, 8, 1), supplier="Acme").price == 70
    with pytest.raises(RateNotFound):
        rb.material_price("TILE-600", date(2025, 1, 1))
