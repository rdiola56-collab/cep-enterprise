"""Estimating module registry.

A module is *configuration*, never calculation code: it declares its item
types, which shared formula measures each one, how each is priced (through an
assembly, or directly from a price-book item for provisional sums), and
module-level validation rules. All arithmetic happens in the shared engine
(``app.engine.service.EstimatingEngine``).

Modules 1–16 are estimating modules. Modules 17–19 are workspaces over the
shared services: 17 and 18 edit the price book and labor/productivity
library; 19 is the master estimate (consolidation, BOQ, summaries).
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
    classification: str  # reporting category inside the module
    default_assembly: str | None = None  # priced through this assembly (overridable per item)
    priced_directly: bool = False  # priced from a price-book item chosen per estimate item (provisional sums)
    rules: tuple[ValidationRule, ...] = ()

    def __post_init__(self):
        if self.priced_directly == (self.default_assembly is not None):
            raise ValueError(f"item type '{self.code}' must be priced by an assembly or directly, not both")


@dataclass(frozen=True)
class ModuleDefinition:
    number: int
    code: str
    name: str
    trade: str
    kind: str = "estimating"  # estimating | library | reporting
    item_types: tuple[ItemType, ...] = ()
    reporting_categories: tuple[str, ...] = ()
    services: tuple[str, ...] = ()  # for library/reporting workspaces: the API areas they use

    @property
    def status(self) -> str:
        if self.kind == "estimating":
            return "configured" if self.item_types else "planned"
        return "configured" if self.services else "planned"

    def item_type(self, code: str) -> ItemType:
        for it in self.item_types:
            if it.code == code:
                return it
        raise KeyError(f"module '{self.code}' has no item type '{code}'")


def _it(code, label, formula, classification, assembly=None, **kw) -> ItemType:
    return ItemType(code, label, formula, classification, assembly, **kw)


_MODULES: list[ModuleDefinition] = [
    ModuleDefinition(1, "reinforced_concrete", "Reinforced Concrete Works", "Structural", item_types=(
        _it("footing", "Footing", "CONCRETE_VOLUME", "Substructure", "RC-FOOTING"),
        _it("column", "Column", "CONCRETE_VOLUME", "Superstructure", "RC-COLUMN"),
        _it("beam", "Beam", "CONCRETE_VOLUME", "Superstructure", "RC-BEAM",
            rules=(ValidationRule("height", max_value=Decimal(3), message="beam depth over 3 m"),)),
        _it("slab", "Suspended slab", "CONCRETE_VOLUME", "Superstructure", "RC-SLAB",
            rules=(ValidationRule("height", max_value=Decimal("0.6"), message="slab thicker than 600 mm"),)),
        _it("wall", "Shear / retaining wall", "CONCRETE_VOLUME", "Superstructure", "RC-WALL"),
    ), reporting_categories=("Substructure", "Superstructure")),
    ModuleDefinition(2, "structural_steel", "Structural Steel", "Structural", item_types=(
        _it("member", "Steel member", "STEEL_WEIGHT", "Framing", "SS-MEMBER"),
        _it("plate", "Base / connection plate", "COUNT", "Connections", "SS-PLATE"),
    ), reporting_categories=("Framing", "Connections")),
    ModuleDefinition(3, "roofing_steel", "Roofing Steel Works", "Structural", item_types=(
        _it("truss", "Roof truss members", "STEEL_WEIGHT", "Trusses", "RS-TRUSS"),
        _it("purlin", "Purlins", "LINEAR_LENGTH", "Purlins", "RS-PURLIN"),
    ), reporting_categories=("Trusses", "Purlins")),
    ModuleDefinition(4, "roofing", "Roofing Works", "Architectural", item_types=(
        _it("roof_sheet", "Roof sheeting", "ROOF_SLOPED_AREA", "Roof covering", "RF-SHEET"),
        _it("gutter", "Gutter", "LINEAR_LENGTH", "Rainwater", "RF-GUTTER"),
        _it("flashing", "Flashing and ridge roll", "LINEAR_LENGTH", "Roof covering", "RF-FLASHING"),
    ), reporting_categories=("Roof covering", "Rainwater")),
    ModuleDefinition(5, "walls_partitions", "Walls and Partitions", "Architectural", item_types=(
        _it("chb_wall", "CHB wall", "WALL_AREA", "Masonry", "WALL-CHB150"),
        _it("drywall", "Drywall partition", "WALL_AREA", "Drywall", "WALL-DRYWALL"),
        _it("plaster", "Plastering", "WALL_AREA", "Plaster", "WALL-PLASTER"),
    ), reporting_categories=("Masonry", "Drywall", "Plaster")),
    ModuleDefinition(6, "room_layout", "Room Layout", "Architectural", item_types=(
        _it("floor_screed", "Floor screed", "FLOOR_AREA", "Floors", "RM-SCREED"),
        _it("skirting", "Skirting", "ROOM_PERIMETER", "Skirting", "RM-SKIRTING"),
    ), reporting_categories=("Floors", "Skirting")),
    ModuleDefinition(7, "ceiling", "Ceiling Works", "Architectural", item_types=(
        _it("ceiling_board", "Ceiling board", "FLOOR_AREA", "Ceilings", "CLG-BOARD"),
        _it("cornice", "Cornice / ceiling trim", "ROOM_PERIMETER", "Trims", "CLG-CORNICE"),
    ), reporting_categories=("Ceilings", "Trims")),
    ModuleDefinition(8, "suspended_ceiling", "Suspended Ceilings", "Architectural", item_types=(
        _it("acoustic_tile", "Acoustic tile on T-runner grid", "FLOOR_AREA", "Suspended ceilings", "SCLG-ACOUSTIC"),
    ), reporting_categories=("Suspended ceilings",)),
    ModuleDefinition(9, "painting", "Painting Works", "Architectural", item_types=(
        _it("wall_paint", "Wall painting", "WALL_AREA", "Walls", "PNT-WALL"),
        _it("ceiling_paint", "Ceiling painting", "FLOOR_AREA", "Ceilings", "PNT-CEILING"),
        _it("steel_paint", "Painting of steel (by weight)", "STEEL_WEIGHT", "Steel", "PNT-STEEL"),
    ), reporting_categories=("Walls", "Ceilings", "Steel")),
    ModuleDefinition(10, "tiling", "Tiling Works", "Architectural", item_types=(
        _it("floor_tile", "Floor tiling", "FLOOR_AREA", "Floor finishes", "TILE-FLR600"),
        _it("wall_tile", "Wall tiling", "WALL_AREA", "Wall finishes", "TILE-WALL300"),
    ), reporting_categories=("Floor finishes", "Wall finishes")),
    ModuleDefinition(11, "doors_windows_cabinets", "Doors, Windows and Cabinets", "Architectural", item_types=(
        _it("door", "Door set", "COUNT", "Doors", "DW-DOOR"),
        _it("window", "Window", "OPENING_AREA", "Windows", "DW-WINDOW"),
        _it("cabinet", "Cabinet run", "LINEAR_LENGTH", "Cabinets", "DW-CABINET"),
    ), reporting_categories=("Doors", "Windows", "Cabinets")),
    ModuleDefinition(12, "parking_finishes", "Parking Finishes", "Architectural", item_types=(
        _it("floor_coating", "Parking floor coating", "FLOOR_AREA", "Floor coating", "PK-COATING"),
        _it("line_marking", "Line marking", "LINEAR_LENGTH", "Markings", "PK-LINE"),
        _it("wheel_stopper", "Wheel stopper", "COUNT", "Accessories", "PK-WHEELSTOP"),
    ), reporting_categories=("Floor coating", "Markings", "Accessories")),
    ModuleDefinition(13, "plumbing_roughins", "Plumbing Rough-ins", "Plumbing", item_types=(
        _it("pipe", "Pipe run", "LINEAR_LENGTH", "Piping", "PL-PIPE"),
        _it("fixture_roughin", "Fixture rough-in", "COUNT", "Rough-ins", "PL-ROUGHIN"),
    ), reporting_categories=("Piping", "Rough-ins")),
    ModuleDefinition(14, "electrical_roughins", "Electrical Rough-ins", "Electrical", item_types=(
        _it("conduit", "Conduit run", "LINEAR_LENGTH", "Conduits", "EL-CONDUIT"),
        _it("box", "Outlet / device box", "COUNT", "Boxes", "EL-BOX"),
    ), reporting_categories=("Conduits", "Boxes")),
    ModuleDefinition(15, "fire_protection_fdas", "Fire Protection and FDAS", "Fire Protection", item_types=(
        _it("sprinkler_pipe", "Sprinkler pipe run", "LINEAR_LENGTH", "Sprinkler piping", "FP-PIPE"),
        _it("sprinkler_head", "Sprinkler head", "COUNT", "Sprinkler heads", "FP-HEAD"),
        _it("fdas_device", "FDAS device", "COUNT", "FDAS", "FDAS-DEVICE"),
    ), reporting_categories=("Sprinkler piping", "Sprinkler heads", "FDAS")),
    ModuleDefinition(16, "provisional_sums", "Provisional Sums", "General", item_types=(
        _it("allowance", "Provisional sum", "LUMP_SUM", "Provisional sums", priced_directly=True),
    ), reporting_categories=("Provisional sums",)),
    ModuleDefinition(17, "materials_pricelist", "Materials and Pricelist", "Library", kind="library",
                     services=("library/materials", "library/prices")),
    ModuleDefinition(18, "labor_productivity", "Labor and Productivity", "Library", kind="library",
                     services=("library/labor-rates", "library/crews", "library/productivity", "library/equipment-rates")),
    ModuleDefinition(19, "detailed_estimate", "Detailed Estimate and Reports", "Reporting", kind="reporting",
                     services=("boq", "summary", "consolidate", "compare")),
]

MODULES: dict[str, ModuleDefinition] = {m.code: m for m in _MODULES}
assert len(MODULES) == 19


def get_module(code: str) -> ModuleDefinition:
    try:
        return MODULES[code]
    except KeyError:
        raise KeyError(f"unknown module '{code}'") from None
