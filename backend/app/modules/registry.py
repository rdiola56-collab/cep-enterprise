"""Estimating module registry.

A module is *configuration*, never calculation code: it declares its item
types, which shared formula measures each one, which assembly prices it, and
module-level validation rules. All arithmetic happens in the shared engine
(``app.engine.service.EstimatingEngine``).

All 19 modules are registered so the UI and API can list them, but only
modules with item types configured are ``status == "configured"``. The rest
are ``"planned"`` and are not presented as working.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ValidationRule:
    """Bound on an input, in the formula input's own unit (module-specific, tighter than the formula's)."""

    input: str
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    message: str = ""


@dataclass(frozen=True)
class ItemType:
    code: str
    label: str
    formula_code: str
    default_assembly: str
    classification: str  # reporting category inside the module
    rules: tuple[ValidationRule, ...] = ()


@dataclass(frozen=True)
class ModuleDefinition:
    number: int
    code: str
    name: str
    trade: str
    item_types: tuple[ItemType, ...] = ()
    reporting_categories: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "configured" if self.item_types else "planned"

    def item_type(self, code: str) -> ItemType:
        for it in self.item_types:
            if it.code == code:
                return it
        raise KeyError(f"module '{self.code}' has no item type '{code}'")


_MODULES: list[ModuleDefinition] = [
    ModuleDefinition(
        1,
        "reinforced_concrete",
        "Reinforced Concrete Works",
        "Structural",
        item_types=(
            ItemType("beam", "Beam", "CONCRETE_VOLUME", "RC-BEAM", "Superstructure",
                     rules=(ValidationRule("height", max_value=Decimal(3), message="beam depth over 3 m"),)),
            ItemType("column", "Column", "CONCRETE_VOLUME", "RC-COLUMN", "Superstructure"),
            ItemType("slab", "Suspended slab", "CONCRETE_VOLUME", "RC-SLAB", "Superstructure",
                     rules=(ValidationRule("height", max_value=Decimal("0.6"), message="slab thicker than 600 mm"),)),
        ),
        reporting_categories=("Substructure", "Superstructure"),
    ),
    ModuleDefinition(2, "structural_steel", "Structural Steel", "Structural",
                     item_types=(ItemType("member", "Steel member", "STEEL_WEIGHT", "SS-MEMBER", "Framing"),),
                     reporting_categories=("Framing", "Connections")),
    ModuleDefinition(3, "roofing_steel", "Roofing Steel Works", "Structural"),
    ModuleDefinition(4, "roofing", "Roofing Works", "Architectural"),
    ModuleDefinition(
        5,
        "walls_partitions",
        "Walls and Partitions",
        "Architectural",
        item_types=(ItemType("chb_wall", "CHB wall", "WALL_AREA", "WALL-CHB150", "Masonry"),),
        reporting_categories=("Masonry", "Drywall"),
    ),
    ModuleDefinition(6, "room_layout", "Room Layout", "Architectural"),
    ModuleDefinition(7, "ceiling", "Ceiling Works", "Architectural",
                     item_types=(ItemType("ceiling_board", "Ceiling board", "FLOOR_AREA", "CLG-BOARD", "Ceilings"),),
                     reporting_categories=("Ceilings",)),
    ModuleDefinition(8, "suspended_ceiling", "Suspended Ceilings", "Architectural"),
    ModuleDefinition(9, "painting", "Painting Works", "Architectural"),
    ModuleDefinition(
        10,
        "tiling",
        "Tiling Works",
        "Architectural",
        item_types=(ItemType("floor_tile", "Floor tiling", "FLOOR_AREA", "TILE-FLR600", "Floor finishes"),),
        reporting_categories=("Floor finishes", "Wall finishes"),
    ),
    ModuleDefinition(11, "doors_windows_cabinets", "Doors, Windows and Cabinets", "Architectural"),
    ModuleDefinition(12, "parking_finishes", "Parking Finishes", "Architectural"),
    ModuleDefinition(13, "plumbing_roughins", "Plumbing Rough-ins", "Plumbing"),
    ModuleDefinition(14, "electrical_roughins", "Electrical Rough-ins", "Electrical"),
    ModuleDefinition(15, "fire_protection_fdas", "Fire Protection and FDAS", "Fire Protection"),
    ModuleDefinition(16, "provisional_sums", "Provisional Sums", "General"),
    ModuleDefinition(17, "materials_pricelist", "Materials and Pricelist", "Library"),
    ModuleDefinition(18, "labor_productivity", "Labor and Productivity", "Library"),
    ModuleDefinition(19, "detailed_estimate", "Detailed Estimate and Reports", "Reporting"),
]

MODULES: dict[str, ModuleDefinition] = {m.code: m for m in _MODULES}
assert len(MODULES) == 19


def get_module(code: str) -> ModuleDefinition:
    try:
        return MODULES[code]
    except KeyError:
        raise KeyError(f"unknown module '{code}'") from None
