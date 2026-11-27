"""Test fixtures: explicit rate records and assemblies used by the engine tests.

These are test inputs with hand-checkable numbers, not application data. The
application reads every rate from the database (see app/services/ratebook.py).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as Dec

from app.engine.assembly import Assembly, EquipmentComponent, LaborComponent, MaterialComponent
from app.engine.formula import FormulaLibrary, standard_formulas
from app.engine.rates import (
    CostParameters,
    Crew,
    CrewMember,
    EquipmentRate,
    InMemoryRateBook,
    LaborRate,
    MaterialPrice,
    Markup,
    Productivity,
)

D0 = date(2026, 1, 1)
AS_OF = date(2026, 11, 1)

PARAMS = CostParameters(
    currency="PHP",
    hours_per_day=Dec(8),
    markups=(
        Markup("overhead", "Overhead", Dec(10), "direct"),
        Markup("contingency", "Contingency", Dec(5), "direct"),
        Markup("profit", "Contractor's profit", Dec(10), "direct"),
        Markup("tax", "VAT", Dec(12), "cumulative"),
    ),
    productivity_factors={"high_rise": Dec("0.8"), "commercial": Dec("0.95"), "difficult_access": Dec("0.7")},
)

NO_MARKUP = CostParameters(currency="PHP", hours_per_day=Dec(8), markups=())


def price(code, unit, value, **kw):
    return MaterialPrice(code, unit, Dec(value), "PHP", kw.pop("valid_from", D0), source_id=kw.pop("source_id", f"price:{code}"), **kw)


def labor(cls, rate):
    return LaborRate(cls, Dec(rate), "PHP", D0, source_id=f"labor:{cls}")


BASE_PRICES = [
    price("TILE-600", "pcs", "85.00"),
    price("ADHESIVE", "bag", "450.00"),
    price("DIESEL", "L", "65.00", category="fuel"),
    price("RMC-4000", "m3", "5200.00"),
    price("REBAR-G60", "t", "52000.00"),
    price("TIE-WIRE", "kg", "95.00"),
    price("PLY-FORM", "pcs", "780.00"),
    price("CHB-150", "pcs", "18.00"),
    price("MORTAR", "m3", "4500.00"),
    price("GYP-BOARD", "pcs", "420.00"),
]

LABOR_RATES = [
    labor("foreman", "100"),
    labor("carpenter", "80"),
    labor("helper", "60"),
    labor("tile_setter", "90"),
    labor("tile_helper", "70"),
    labor("steelman", "85"),
    labor("mason", "85"),
]

CREWS = [
    Crew("CARP-5", "Carpentry crew", (CrewMember("foreman", Dec(1)), CrewMember("carpenter", Dec(2)), CrewMember("helper", Dec(2)))),
    Crew("TILE-2", "Tiling crew", (CrewMember("tile_setter", Dec(1)), CrewMember("tile_helper", Dec(1)))),
    Crew("STEEL-3", "Rebar crew", (CrewMember("steelman", Dec(2)), CrewMember("helper", Dec(1)))),
    Crew("CONC-6", "Concreting crew", (CrewMember("foreman", Dec(1)), CrewMember("helper", Dec(5)))),
    Crew("MASON-2", "Masonry crew", (CrewMember("mason", Dec(1)), CrewMember("helper", Dec(1)))),
]

PRODUCTIVITIES = [
    Productivity("FORMWORK", "normal", Dec(20), "m2", "prod:FORMWORK"),
    Productivity("FORMWORK", "basement", Dec(15), "m2", "prod:FORMWORK:basement"),
    Productivity("TILING", "normal", Dec(12), "m2", "prod:TILING"),
    Productivity("REBAR-FIX", "normal", Dec(1), "t", "prod:REBAR-FIX"),
    Productivity("CONC-POUR", "normal", Dec(30), "m3", "prod:CONC-POUR"),
    Productivity("CHB-LAY", "normal", Dec(10), "m2", "prod:CHB-LAY"),
    Productivity("CEIL-BOARD", "normal", Dec(25), "m2", "prod:CEIL-BOARD"),
    Productivity("RC-FORM-M3", "normal", Dec(4), "m3", "prod:RC-FORM-M3"),
    Productivity("RC-REBAR-M3", "normal", Dec(6), "m3", "prod:RC-REBAR-M3"),
]

EQUIPMENT = [
    EquipmentRate("TILE-CUTTER", Dec(40), "PHP", D0, operating_per_hour=Dec(5), source_id="equip:TILE-CUTTER"),
    EquipmentRate("VIBRATOR", Dec(60), "PHP", D0, fuel_litres_per_hour=Dec("0.8"), fuel_item_code="DIESEL",
                  operating_per_hour=Dec(10), source_id="equip:VIBRATOR"),
    EquipmentRate("TOWER-CRANE", Dec(2500), "PHP", D0, fuel_litres_per_hour=Dec(0), operating_per_hour=Dec(300),
                  source_id="equip:TOWER-CRANE"),
]


def ratebook(params: CostParameters = PARAMS, prices=None, productivities=None) -> InMemoryRateBook:
    return InMemoryRateBook(
        params,
        prices=BASE_PRICES if prices is None else prices,
        labor_rates=LABOR_RATES,
        crews=CREWS,
        productivities=PRODUCTIVITIES if productivities is None else productivities,
        equipment_rates=EQUIPMENT,
    )


TILE_FLOOR = Assembly(
    code="TILE-FLR600",
    description="600×600 ceramic floor tiles on adhesive",
    trade="Architectural",
    unit="m2",
    materials=(
        MaterialComponent("TILE-600", "Ceramic tile 600×600", Dec("2.78"), "pcs", wastage_pct=Dec(5)),
        MaterialComponent("ADHESIVE", "Tile adhesive 25 kg", Dec("0.2"), "bag"),
    ),
    labor=(LaborComponent("tiling", "TILE-2", "TILING"),),
    equipment=(EquipmentComponent("TILE-CUTTER", "Tile cutter", "per_unit", hours_per_unit=Dec("0.05")),),
)

FORMWORK = Assembly(
    code="FORMWORK",
    description="Formworks, plywood",
    trade="Structural",
    unit="m2",
    labor=(LaborComponent("formworks", "CARP-5", "FORMWORK"),),
)

RC_BEAM = Assembly(
    code="RC-BEAM",
    description="Reinforced concrete beam",
    trade="Structural",
    unit="m3",
    materials=(
        MaterialComponent("RMC-4000", "Ready-mix concrete 4000 psi", Dec(1), "m3", wastage_pct=Dec(5)),
        MaterialComponent("REBAR-G60", "Reinforcing bars Grade 60", Dec(150), "kg", wastage_pct=Dec(7)),
        MaterialComponent("TIE-WIRE", "Tie wire #16", Dec("1.5"), "kg", wastage_pct=Dec(10)),
        MaterialComponent("PLY-FORM", "Formwork plywood 3/4\"", Dec("2.5"), "pcs", wastage_pct=Dec(0)),
    ),
    labor=(
        LaborComponent("formworks", "CARP-5", "RC-FORM-M3"),
        LaborComponent("rebar", "STEEL-3", "RC-REBAR-M3"),
        LaborComponent("placing", "CONC-6", "CONC-POUR"),
    ),
    equipment=(
        EquipmentComponent("VIBRATOR", "Concrete vibrator", "labor_time", labor_task="placing"),
        EquipmentComponent("TOWER-CRANE", "Tower crane share", "per_unit", hours_per_unit=Dec("0.1")),
    ),
)

CHB_WALL = Assembly(
    code="WALL-CHB150",
    description="150 mm CHB wall",
    trade="Architectural",
    unit="m2",
    materials=(
        MaterialComponent("CHB-150", "Concrete hollow block 150 mm", Dec("12.5"), "pcs", wastage_pct=Dec(5)),
        MaterialComponent("MORTAR", "Mortar", Dec("0.015"), "m3", wastage_pct=Dec(10)),
    ),
    labor=(LaborComponent("laying", "MASON-2", "CHB-LAY"),),
)

CEILING_BOARD = Assembly(
    code="CLG-BOARD",
    description="Gypsum board ceiling",
    trade="Architectural",
    unit="m2",
    materials=(MaterialComponent("GYP-BOARD", "Gypsum board 1.2×2.4", Dec("0.35"), "pcs", wastage_pct=Dec(5)),),
    labor=(LaborComponent("boarding", "CARP-5", "CEIL-BOARD"),),
)

ASSEMBLIES = {a.code: a for a in (TILE_FLOOR, FORMWORK, RC_BEAM, CHB_WALL, CEILING_BOARD)}


def formula_library() -> FormulaLibrary:
    lib = FormulaLibrary()
    for f in standard_formulas():
        lib.publish(f)
    return lib
