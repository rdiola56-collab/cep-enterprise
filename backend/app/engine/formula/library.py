"""Versioned formula library.

Publishing a changed formula creates a new immutable version; earlier versions
remain resolvable forever so past calculations can be reproduced exactly.
The database-backed library (``app.services.formulas``) follows the same rules;
this in-process library is what the engine and its tests use directly.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .evaluator import FormulaDefinition, FormulaDefinitionError, InputSpec


class FormulaLibrary:
    def __init__(self) -> None:
        self._versions: dict[str, list[FormulaDefinition]] = {}

    def publish(self, definition: FormulaDefinition) -> FormulaDefinition:
        history = self._versions.setdefault(definition.code, [])
        candidate = replace(definition, version=len(history) + 1, _ast=None).validate()
        if history and history[-1].content_hash == candidate.content_hash:
            raise FormulaDefinitionError(f"formula '{definition.code}' is unchanged from version {len(history)}")
        history.append(candidate)
        return candidate

    def get(self, code: str, version: int | None = None) -> FormulaDefinition:
        history = self._versions.get(code)
        if not history:
            raise KeyError(f"unknown formula '{code}'")
        if version is None:
            return history[-1]
        if not 1 <= version <= len(history):
            raise KeyError(f"formula '{code}' has no version {version}")
        return history[version - 1]

    def history(self, code: str) -> list[FormulaDefinition]:
        return list(self._versions.get(code, []))

    def codes(self) -> list[str]:
        return sorted(self._versions)


def standard_formulas() -> list[FormulaDefinition]:
    """The reference formulas from the CEP specification, as definitions.

    These are formula *definitions* (methodology), not prices or rates; they
    are seeded into the database so estimators can reuse or revise them.
    """
    L = lambda name, label, **kw: InputSpec(name, "length", "m", label, **kw)  # noqa: E731
    N = lambda name, label, **kw: InputSpec(name, "dimensionless", "pcs", label, **kw)  # noqa: E731
    return [
        FormulaDefinition(
            code="CONCRETE_VOLUME",
            name="Concrete volume",
            expression="length × width × height × pieces",
            output_kind="volume",
            output_unit="m3",
            inputs=(
                L("length", "Length", min_value=Decimal(0)),
                L("width", "Width", min_value=Decimal(0)),
                L("height", "Height / depth", min_value=Decimal(0)),
                N("pieces", "Number of pieces", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="FLOOR_AREA",
            name="Floor area",
            expression="length × width × repetitions",
            output_kind="area",
            output_unit="m2",
            inputs=(
                L("length", "Length", min_value=Decimal(0)),
                L("width", "Width", min_value=Decimal(0)),
                N("repetitions", "Number of repetitions", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="WALL_AREA",
            name="Wall area less openings",
            expression="length × height − openings",
            output_kind="area",
            output_unit="m2",
            inputs=(
                L("length", "Wall length", min_value=Decimal(0)),
                L("height", "Wall height", min_value=Decimal(0)),
                InputSpec("openings", "area", "m2", "Total area of openings", default=Decimal(0), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="STEEL_WEIGHT",
            name="Steel weight",
            expression="section_weight × length",
            output_kind="mass",
            output_unit="kg",
            inputs=(
                InputSpec("section_weight", "linear_mass", "kg/m", "Section weight per metre", min_value=Decimal(0)),
                L("length", "Length", min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="TILE_QUANTITY",
            name="Tile quantity",
            expression="floor_area ÷ tile_coverage",
            output_kind="dimensionless",
            output_unit="pcs",
            inputs=(
                InputSpec("floor_area", "area", "m2", "Floor area", min_value=Decimal(0)),
                InputSpec("tile_coverage", "area", "m2", "Coverage per tile", min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="REBAR_BAR_COUNT",
            name="Number of bars at spacing",
            expression="ceil(span ÷ spacing) + 1",
            output_kind="dimensionless",
            output_unit="pcs",
            inputs=(L("span", "Distribution length", min_value=Decimal(0)), L("spacing", "Bar spacing", min_value=Decimal(0))),
        ),
        FormulaDefinition(
            code="REBAR_WEIGHT",
            name="Rebar weight",
            expression="bars × cutting_length × unit_weight",
            output_kind="mass",
            output_unit="kg",
            inputs=(
                N("bars", "Number of bars", min_value=Decimal(0)),
                L("cutting_length", "Cutting length per bar", min_value=Decimal(0)),
                InputSpec("unit_weight", "linear_mass", "kg/m", "Unit weight per metre", min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="LINEAR_LENGTH",
            name="Linear length",
            expression="length × repetitions",
            output_kind="length",
            output_unit="m",
            inputs=(
                L("length", "Length per run", min_value=Decimal(0)),
                N("repetitions", "Number of runs", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="COUNT",
            name="Count of items",
            expression="count × repetitions",
            output_kind="dimensionless",
            output_unit="pcs",
            inputs=(
                N("count", "Number of items", min_value=Decimal(0)),
                N("repetitions", "Repetitions (floors, units…)", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="ROOM_PERIMETER",
            name="Room perimeter less openings",
            expression="(2 × (length + width) − deductions) × repetitions",
            output_kind="length",
            output_unit="m",
            inputs=(
                L("length", "Room length", min_value=Decimal(0)),
                L("width", "Room width", min_value=Decimal(0)),
                L("deductions", "Door widths and other deductions", default=Decimal(0), min_value=Decimal(0)),
                N("repetitions", "Number of identical rooms", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="ROOF_SLOPED_AREA",
            name="Sloped roof area",
            expression="length × sqrt(run^2 + rise^2) × slopes",
            output_kind="area",
            output_unit="m2",
            inputs=(
                L("length", "Length along the ridge", min_value=Decimal(0)),
                L("run", "Horizontal run of one slope", min_value=Decimal(0)),
                L("rise", "Rise of one slope", min_value=Decimal(0)),
                N("slopes", "Number of roof slopes", default=Decimal(2), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="OPENING_AREA",
            name="Opening area",
            expression="width × height × count",
            output_kind="area",
            output_unit="m2",
            inputs=(
                L("width", "Opening width", min_value=Decimal(0)),
                L("height", "Opening height", min_value=Decimal(0)),
                N("count", "Number of openings", default=Decimal(1), min_value=Decimal(0)),
            ),
        ),
        FormulaDefinition(
            code="LUMP_SUM",
            name="Lump sum quantity",
            expression="lots",
            output_kind="dimensionless",
            output_unit="lot",
            inputs=(InputSpec("lots", "dimensionless", "lot", "Number of lots", default=Decimal(1), min_value=Decimal(0)),),
        ),
        *measured_formulas(),
    ]


# Output kind of an item type's formula → the formula used when that quantity is
# measured directly (from a drawing takeoff or a BIM quantity set) instead of from dimensions.
MEASURED_FORMULA_FOR_KIND = {
    "length": "MEASURED_LENGTH",
    "area": "MEASURED_AREA",
    "volume": "MEASURED_VOLUME",
    "mass": "MEASURED_MASS",
    "dimensionless": "MEASURED_COUNT",
}


def measured_formulas() -> list[FormulaDefinition]:
    units = {"length": "m", "area": "m2", "volume": "m3", "mass": "kg", "dimensionless": "pcs"}
    return [
        FormulaDefinition(
            code=code,
            name=f"Measured {kind if kind != 'dimensionless' else 'count'}",
            expression="measured",
            output_kind=kind,
            output_unit=units[kind],
            inputs=(InputSpec("measured", kind, units[kind], "Measured quantity (takeoff or model)", min_value=Decimal(0)),),
            description="Quantity measured directly on a drawing or taken from a BIM quantity set.",
        )
        for kind, code in MEASURED_FORMULA_FOR_KIND.items()
    ]
