"""Construction assemblies: reusable recipes of materials, labor and equipment per unit of work."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from .units import get_unit


class AssemblyError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialComponent:
    item_code: str
    description: str
    consumption: Decimal  # net quantity of material per one assembly unit
    unit: str  # unit of `consumption`
    wastage_pct: Decimal = Decimal(0)
    supplier: str | None = None
    brand: str | None = None
    category: str = "material"  # material | subcontract | other


@dataclass(frozen=True)
class LaborComponent:
    task: str  # e.g. "formworks", "rebar fixing"; referenced by equipment on labor_time basis
    crew_code: str
    productivity_code: str


@dataclass(frozen=True)
class EquipmentComponent:
    equipment_code: str
    description: str
    basis: str  # "per_unit": hours_per_unit × quantity; "labor_time": runs as long as a labor task
    hours_per_unit: Decimal | None = None
    labor_task: str | None = None
    count: Decimal = Decimal(1)


@dataclass(frozen=True)
class Assembly:
    code: str
    description: str
    trade: str
    unit: str
    revision: int = 1
    materials: tuple[MaterialComponent, ...] = ()
    labor: tuple[LaborComponent, ...] = ()
    equipment: tuple[EquipmentComponent, ...] = ()
    project_id: str | None = None  # None = master library; set when customized for a project
    parent_revision: str | None = None  # "<code>@<revision>" this was derived from
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> "Assembly":
        get_unit(self.unit)
        tasks = [l.task for l in self.labor]
        if len(set(tasks)) != len(tasks):
            raise AssemblyError(f"{self.code}: duplicate labor task names")
        for m in self.materials:
            get_unit(m.unit)
            if m.consumption < 0:
                raise AssemblyError(f"{self.code}: negative consumption for {m.item_code}")
            if not Decimal(0) <= m.wastage_pct < Decimal(100):
                raise AssemblyError(f"{self.code}: wastage for {m.item_code} must be 0–100%")
            if m.category not in ("material", "subcontract", "other"):
                raise AssemblyError(f"{self.code}: unknown category {m.category}")
        for e in self.equipment:
            if e.basis == "per_unit":
                if e.hours_per_unit is None or e.hours_per_unit < 0:
                    raise AssemblyError(f"{self.code}: {e.equipment_code} needs hours_per_unit")
            elif e.basis == "labor_time":
                if e.labor_task not in tasks:
                    raise AssemblyError(f"{self.code}: {e.equipment_code} refers to unknown labor task '{e.labor_task}'")
            else:
                raise AssemblyError(f"{self.code}: unknown equipment basis '{e.basis}'")
        return self

    @property
    def ref(self) -> str:
        scope = f"project:{self.project_id}" if self.project_id else "master"
        return f"{self.code}@{self.revision}[{scope}]"

    def customize_for_project(self, project_id: str, **changes) -> "Assembly":
        """Derive a project-specific revision. The master assembly is untouched."""
        return replace(
            self,
            project_id=project_id,
            revision=1 if self.project_id != project_id else self.revision + 1,
            parent_revision=f"{self.code}@{self.revision}",
            **changes,
        ).validate()
