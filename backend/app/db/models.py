"""Centralised relational schema (PostgreSQL).

Conventions
- ``project_id`` NULL on a library table means the master library; a value
  means a project-specific record that overrides the master for that project.
- Rates, prices, formula versions and assembly revisions are append-only:
  a change is a new row with a higher revision. Database triggers (see the
  initial migration) enforce this, and freeze approved estimate versions.
- Estimate items, cost components and takeoffs carry ``project_id`` with a
  composite foreign key to their parent, so a row can never be attached to
  another project's estimate.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

QTY = Numeric(28, 10)
MONEY = Numeric(24, 6)
PCT = Numeric(9, 4)


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def project_fk(nullable: bool) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=nullable, index=True)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def library_unique(name: str, *cols: str) -> UniqueConstraint:
    # NULLS NOT DISTINCT: one master row and one row per project for each code.
    return UniqueConstraint("project_id", *cols, name=name, postgresql_nulls_not_distinct=True)


# ---------------------------------------------------------------- project & WBS


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    unit_system: Mapped[str] = mapped_column(String(10), default="metric")
    created_at: Mapped[datetime] = created()
    __table_args__ = (CheckConstraint("unit_system in ('metric','imperial')", name="ck_project_unit_system"),)


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("project_id", "code"), UniqueConstraint("id", "project_id"))


class Floor(Base):
    __tablename__ = "floors"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    level: Mapped[int] = mapped_column(Integer)
    is_basement: Mapped[bool] = mapped_column(Boolean, default=False)
    gross_floor_area_m2: Mapped[Decimal | None] = mapped_column(QTY)
    __table_args__ = (
        ForeignKeyConstraint(["building_id", "project_id"], ["buildings.id", "buildings.project_id"]),
        UniqueConstraint("building_id", "code"),
        UniqueConstraint("id", "project_id"),
    )


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    floor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (
        ForeignKeyConstraint(["floor_id", "project_id"], ["floors.id", "floors.project_id"]),
        UniqueConstraint("floor_id", "code"),
        UniqueConstraint("id", "project_id"),
    )


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    floor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (
        ForeignKeyConstraint(["floor_id", "project_id"], ["floors.id", "floors.project_id"]),
        ForeignKeyConstraint(["zone_id", "project_id"], ["zones.id", "zones.project_id"]),
        UniqueConstraint("floor_id", "code"),
        UniqueConstraint("id", "project_id"),
    )


# ---------------------------------------------------------------- formulas


class Formula(Base):
    __tablename__ = "formulas"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created()
    versions: Mapped[list["FormulaVersion"]] = relationship(back_populates="formula", order_by="FormulaVersion.version")
    __table_args__ = (library_unique("uq_formula_code", "code"),)


class FormulaVersion(Base):
    __tablename__ = "formula_versions"
    id: Mapped[uuid.UUID] = pk()
    formula_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("formulas.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    expression: Mapped[str] = mapped_column(Text)
    output_kind: Mapped[str] = mapped_column(String(30))
    output_unit: Mapped[str] = mapped_column(String(20))
    inputs: Mapped[list] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = created()
    formula: Mapped[Formula] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("formula_id", "version"), CheckConstraint("version >= 1"))


# ---------------------------------------------------------------- resources


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (library_unique("uq_supplier_code", "code"),)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(20), default="material")
    __table_args__ = (
        library_unique("uq_material_code", "code"),
        CheckConstraint("category in ('material','fuel','subcontract','other')", name="ck_material_category"),
    )


class MaterialPrice(Base):
    __tablename__ = "material_prices"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    material_code: Mapped[str] = mapped_column(String(60), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    brand: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(QTY)
    currency: Mapped[str] = mapped_column(String(3))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(60), default="manual")
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_price_nonneg"),
        CheckConstraint("valid_to is null or valid_to >= valid_from", name="ck_price_validity"),
        Index("ix_material_price_lookup", "material_code", "project_id", "valid_from"),
    )


class LaborClass(Base):
    __tablename__ = "labor_classes"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (library_unique("uq_labor_class_code", "code"),)


class LaborRate(Base):
    __tablename__ = "labor_rates"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    labor_class_code: Mapped[str] = mapped_column(String(60), index=True)
    hourly_rate: Mapped[Decimal] = mapped_column(QTY)
    currency: Mapped[str] = mapped_column(String(3))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created()
    __table_args__ = (CheckConstraint("hourly_rate >= 0", name="ck_labor_rate_nonneg"),)


class CrewComposition(Base):
    __tablename__ = "crew_compositions"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    members: Mapped[list["CrewMember"]] = relationship(cascade="all, delete-orphan", order_by="CrewMember.sort")
    __table_args__ = (library_unique("uq_crew_code_rev", "code", "revision"),)


class CrewMember(Base):
    __tablename__ = "crew_members"
    id: Mapped[uuid.UUID] = pk()
    crew_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crew_compositions.id", ondelete="CASCADE"), index=True)
    labor_class_code: Mapped[str] = mapped_column(String(60))
    count: Mapped[Decimal] = mapped_column(QTY)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("count > 0", name="ck_crew_member_count"),)


class ProductivityRate(Base):
    __tablename__ = "productivity_rates"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    condition: Mapped[str] = mapped_column(String(30), default="normal")
    output_per_crew_day: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        library_unique("uq_productivity", "code", "condition", "revision"),
        CheckConstraint("output_per_crew_day > 0", name="ck_productivity_positive"),
        CheckConstraint(
            "condition in ('normal','high_rise','commercial','basement','difficult_access')", name="ck_productivity_condition"
        ),
    )


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    __table_args__ = (library_unique("uq_equipment_code", "code"),)


class EquipmentRate(Base):
    __tablename__ = "equipment_rates"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    equipment_code: Mapped[str] = mapped_column(String(60), index=True)
    rental_per_hour: Mapped[Decimal] = mapped_column(QTY)
    fuel_litres_per_hour: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    fuel_item_code: Mapped[str | None] = mapped_column(String(60))
    operating_per_hour: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    currency: Mapped[str] = mapped_column(String(3))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created()


class CostParameterSet(Base):
    __tablename__ = "cost_parameters"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(3))
    hours_per_day: Mapped[Decimal] = mapped_column(QTY)
    markups: Mapped[list] = mapped_column(JSONB)  # [{code,label,pct,basis}]
    productivity_factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    money_places: Mapped[int] = mapped_column(Integer, default=2)
    quantity_places: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = created()
    __table_args__ = (library_unique("uq_cost_params_rev", "revision"),)


# ---------------------------------------------------------------- assemblies


class AssemblyRecord(Base):
    __tablename__ = "assemblies"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    trade: Mapped[str] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(20))
    revisions: Mapped[list["AssemblyRevision"]] = relationship(order_by="AssemblyRevision.revision")
    __table_args__ = (library_unique("uq_assembly_code", "code"),)


class AssemblyRevision(Base):
    __tablename__ = "assembly_revisions"
    id: Mapped[uuid.UUID] = pk()
    assembly_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assemblies.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assembly_revisions.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created()
    assembly: Mapped[AssemblyRecord] = relationship(back_populates="revisions")
    components: Mapped[list["AssemblyComponent"]] = relationship(order_by="AssemblyComponent.sort")
    __table_args__ = (UniqueConstraint("assembly_id", "revision"),)


class AssemblyComponent(Base):
    __tablename__ = "assembly_components"
    id: Mapped[uuid.UUID] = pk()
    assembly_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assembly_revisions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # material | labor | equipment
    sort: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(300), default="")
    # material
    item_code: Mapped[str | None] = mapped_column(String(60))
    consumption: Mapped[Decimal | None] = mapped_column(QTY)
    unit: Mapped[str | None] = mapped_column(String(20))
    wastage_pct: Mapped[Decimal | None] = mapped_column(PCT)
    category: Mapped[str | None] = mapped_column(String(20))
    supplier_code: Mapped[str | None] = mapped_column(String(60))
    brand: Mapped[str | None] = mapped_column(String(120))
    # labor
    task: Mapped[str | None] = mapped_column(String(60))
    crew_code: Mapped[str | None] = mapped_column(String(60))
    productivity_code: Mapped[str | None] = mapped_column(String(60))
    # equipment
    equipment_code: Mapped[str | None] = mapped_column(String(60))
    basis: Mapped[str | None] = mapped_column(String(20))
    hours_per_unit: Mapped[Decimal | None] = mapped_column(QTY)
    labor_task: Mapped[str | None] = mapped_column(String(60))
    count: Mapped[Decimal | None] = mapped_column(QTY)
    __table_args__ = (CheckConstraint("kind in ('material','labor','equipment')", name="ck_component_kind"),)


# ---------------------------------------------------------------- estimates


class EstimateRecord(Base):
    __tablename__ = "estimates"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("project_id", "code"), UniqueConstraint("id", "project_id"))


class EstimateVersion(Base):
    __tablename__ = "estimate_versions"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    pricing_date: Mapped[date] = mapped_column(Date)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("estimate_versions.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(120))
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        ForeignKeyConstraint(["estimate_id", "project_id"], ["estimates.id", "estimates.project_id"]),
        UniqueConstraint("estimate_id", "version_no"),
        UniqueConstraint("id", "project_id"),
        CheckConstraint("status in ('draft','approved','superseded')", name="ck_version_status"),
    )


class EstimateItemRecord(Base):
    __tablename__ = "estimate_items"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    item_key: Mapped[str] = mapped_column(String(120))  # stable across versions and recalculations
    module: Mapped[str] = mapped_column(String(40))
    item_type: Mapped[str] = mapped_column(String(40))
    trade: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    building_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    floor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    room_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assembly_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assembly_revisions.id"))
    priced_item_code: Mapped[str | None] = mapped_column(String(60))  # direct-priced items (provisional sums)
    formula_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("formula_versions.id"))
    # "formula": dimensions → the item type's formula; "measured": a quantity measured on a drawing/model
    quantity_basis: Mapped[str] = mapped_column(String(20), default="formula", server_default="formula")
    condition: Mapped[str] = mapped_column(String(30))
    inputs: Mapped[dict] = mapped_column(JSONB)  # raw measured inputs as entered
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    unit_rate: Mapped[Decimal] = mapped_column(MONEY)
    breakdown: Mapped[dict] = mapped_column(JSONB)  # full-precision CostBreakdown snapshot
    updated_at: Mapped[datetime] = created()
    __table_args__ = (
        ForeignKeyConstraint(["estimate_version_id", "project_id"], ["estimate_versions.id", "estimate_versions.project_id"]),
        ForeignKeyConstraint(["building_id", "project_id"], ["buildings.id", "buildings.project_id"]),
        ForeignKeyConstraint(["floor_id", "project_id"], ["floors.id", "floors.project_id"]),
        ForeignKeyConstraint(["zone_id", "project_id"], ["zones.id", "zones.project_id"]),
        ForeignKeyConstraint(["room_id", "project_id"], ["rooms.id", "rooms.project_id"]),
        UniqueConstraint("estimate_version_id", "item_key", name="uq_item_key_per_version"),
        CheckConstraint("(assembly_revision_id IS NULL) <> (priced_item_code IS NULL)", name="ck_item_priced_by_one_source"),
        UniqueConstraint("id", "project_id"),
        Index("ix_items_version_trade", "estimate_version_id", "trade"),
    )


class QuantityTakeoff(Base):
    __tablename__ = "quantity_takeoffs"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    formula_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("formula_versions.id"))
    source: Mapped[str] = mapped_column(String(40), default="manual")  # manual | import:<adapter>
    drawing_reference_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drawing_references.id"))
    bim_element_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bim_elements.id"))
    methodology: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = created()
    dimensions: Mapped[list["QuantityDimension"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (
        ForeignKeyConstraint(["estimate_item_id", "project_id"], ["estimate_items.id", "estimate_items.project_id"], ondelete="CASCADE"),
    )


class QuantityDimension(Base):
    __tablename__ = "quantity_dimensions"
    id: Mapped[uuid.UUID] = pk()
    takeoff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quantity_takeoffs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    value: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(20))
    si_value: Mapped[Decimal] = mapped_column(QTY)


class CostComponent(Base):
    __tablename__ = "cost_components"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    category: Mapped[str] = mapped_column(String(20))  # material | labor | equipment | subcontract | other | markup
    code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(20))
    unit_cost: Mapped[Decimal] = mapped_column(QTY)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    source_ref: Mapped[str] = mapped_column(String(200), default="")  # price/rate record used
    __table_args__ = (
        ForeignKeyConstraint(["estimate_item_id", "project_id"], ["estimate_items.id", "estimate_items.project_id"], ondelete="CASCADE"),
        Index("ix_cost_components_category", "project_id", "category", "code"),
    )


class BoqItem(Base):
    """BOQ lines materialised when an estimate version is approved."""

    __tablename__ = "boq_items"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    trade: Mapped[str] = mapped_column(String(60))
    assembly_code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit_rate: Mapped[Decimal] = mapped_column(MONEY)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    source_item_keys: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(["estimate_version_id", "project_id"], ["estimate_versions.id", "estimate_versions.project_id"]),
        UniqueConstraint("estimate_version_id", "line_no"),
    )


# ---------------------------------------------------------------- users and access


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    global_role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)  # bump to revoke all tokens
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        CheckConstraint("global_role in ('admin','cost_engineer','estimator','viewer')", name="ck_user_global_role"),
        CheckConstraint("email = lower(email)", name="ck_user_email_lower"),
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        UniqueConstraint("project_id", "user_id"),
        CheckConstraint("role in ('manager','estimator','approver','viewer')", name="ck_member_role"),
    )


# ---------------------------------------------------------------- drawing / BIM references (schema only; adapters are phase 5)


class DrawingReference(Base):
    __tablename__ = "drawing_references"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    drawing_name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[str] = mapped_column(String(40))
    page: Mapped[int | None] = mapped_column(Integer)
    file_uri: Mapped[str | None] = mapped_column(Text)
    source_markup_id: Mapped[str | None] = mapped_column(String(200))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = created()


class BimElement(Base):
    __tablename__ = "bim_elements"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    model_name: Mapped[str] = mapped_column(String(200))
    model_revision: Mapped[str] = mapped_column(String(40))
    element_guid: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(120))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("project_id", "model_name", "model_revision", "element_guid"),)


# ---------------------------------------------------------------- drawing / BIM import pipeline


class ImportMappingRule(Base):
    """Project configuration: which drawing markups / layers / BIM elements become which item type,
    and which measured value feeds which formula input. Rules hold no prices and no arithmetic."""

    __tablename__ = "import_mapping_rules"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    code: Mapped[str] = mapped_column(String(60))
    adapter: Mapped[str] = mapped_column(String(30))  # takeoff_csv | dxf | ifc
    match_group: Mapped[str] = mapped_column(String(200))  # glob on subject / layer / IFC class
    match_label: Mapped[str | None] = mapped_column(String(200))  # optional glob on label / block / type name
    match_properties: Mapped[dict] = mapped_column(JSONB, default=dict)  # exact property values
    module: Mapped[str] = mapped_column(String(40))
    item_type: Mapped[str] = mapped_column(String(40))
    assembly_code: Mapped[str | None] = mapped_column(String(60))
    priced_item_code: Mapped[str | None] = mapped_column(String(60))
    quantity_basis: Mapped[str] = mapped_column(String(20), default="measured")
    input_map: Mapped[dict] = mapped_column(JSONB)  # formula input → measure name, or {"value", "unit"}
    condition: Mapped[str] = mapped_column(String(30), default="normal")
    aggregate: Mapped[bool] = mapped_column(Boolean, default=False)  # one item per rule and floor
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_import_rule_code"),
        CheckConstraint("adapter in ('takeoff_csv','dxf','ifc')", name="ck_import_rule_adapter"),
        CheckConstraint("quantity_basis in ('formula','measured')", name="ck_import_rule_basis"),
        CheckConstraint("NOT aggregate OR quantity_basis = 'measured'", name="ck_import_rule_aggregate_measured"),
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    adapter: Mapped[str] = mapped_column(String(30))
    source_name: Mapped[str] = mapped_column(String(200))  # drawing set or model name: the item-key namespace
    source_revision: Mapped[str] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="staged")  # staged | committed | discarded
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = created()
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["estimate_version_id", "project_id"], ["estimate_versions.id", "estimate_versions.project_id"]),
        UniqueConstraint("id", "project_id"),
        CheckConstraint("status in ('staged','committed','discarded')", name="ck_import_batch_status"),
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    source_ref: Mapped[str] = mapped_column(String(200))  # markup id / entity handle / IFC GlobalId
    group: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(300), default="")
    level: Mapped[str | None] = mapped_column(String(120))  # page label / storey, matched to a WBS floor
    measures: Mapped[dict] = mapped_column(JSONB, default=dict)  # name → {"value", "unit"}
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20))  # mapped | unmapped | invalid | excluded | imported
    read_errors: Mapped[list] = mapped_column(JSONB, default=list)  # problems reading the source row itself
    messages: Mapped[list] = mapped_column(JSONB, default=list)  # mapping / validation results ("note: …" = advisory)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_mapping_rules.id"))
    rule_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    item_key: Mapped[str | None] = mapped_column(String(120))
    floor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    preview: Mapped[dict | None] = mapped_column(JSONB)  # quantity / unit / amount the engine computed
    drawing_reference_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drawing_references.id"))
    bim_element_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bim_elements.id"))
    __table_args__ = (
        ForeignKeyConstraint(["batch_id", "project_id"], ["import_batches.id", "import_batches.project_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["floor_id", "project_id"], ["floors.id", "floors.project_id"]),
        CheckConstraint("status in ('mapped','unmapped','invalid','excluded','imported')", name="ck_import_row_status"),
        Index("ix_import_rows_batch_row", "batch_id", "row_no"),
    )


# ---------------------------------------------------------------- reports, approvals, audit


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    estimate_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("estimate_versions.id"))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = project_fk(False)
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    decision: Mapped[str] = mapped_column(String(12))
    actor: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID | None] = project_fk(True)
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = created()
    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)


# Triggers enforcing append-only rates and frozen approved estimates (installed by the initial migration).
IMMUTABILITY_SQL = r"""
CREATE OR REPLACE FUNCTION cep_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'CEP: % rows are append-only; create a new revision instead', TG_TABLE_NAME USING ERRCODE = 'P0001';
  END IF;
  -- Only closing a validity window (valid_to) is allowed on an existing rate row.
  IF (to_jsonb(NEW) - 'valid_to') IS DISTINCT FROM (to_jsonb(OLD) - 'valid_to') THEN
    RAISE EXCEPTION 'CEP: % rows are append-only; create a new revision instead', TG_TABLE_NAME USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cep_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'CEP: % rows cannot be changed or deleted', TG_TABLE_NAME USING ERRCODE = 'P0001';
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cep_version_guard() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'approved' THEN
    -- An approved version may only be marked superseded; nothing else about it can change.
    IF TG_OP = 'DELETE' OR NEW.status <> 'superseded'
       OR (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
      RAISE EXCEPTION 'CEP: approved estimate version % is immutable', OLD.id USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF OLD.status = 'superseded' THEN
    RAISE EXCEPTION 'CEP: superseded estimate version % is immutable', OLD.id USING ERRCODE = 'P0001';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cep_frozen_child() RETURNS trigger AS $$
DECLARE
  vid uuid;
  st text;
BEGIN
  IF TG_TABLE_NAME = 'estimate_items' OR TG_TABLE_NAME = 'boq_items' THEN
    vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.estimate_version_id ELSE NEW.estimate_version_id END;
  ELSE
    SELECT i.estimate_version_id INTO vid FROM estimate_items i
     WHERE i.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.estimate_item_id ELSE NEW.estimate_item_id END;
  END IF;
  SELECT status INTO st FROM estimate_versions WHERE id = vid;
  IF st IS NOT NULL AND st <> 'draft' THEN
    RAISE EXCEPTION 'CEP: estimate version % is % and cannot be modified', vid, st USING ERRCODE = 'P0001';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['material_prices','labor_rates','equipment_rates','productivity_rates'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cep_append_only()', t, t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['formula_versions','assembly_revisions','assembly_components','cost_parameters','audit_logs','approvals'] LOOP
    EXECUTE format('CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cep_immutable()', t, t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['estimate_items','cost_components','quantity_takeoffs','boq_items'] LOOP
    EXECUTE format('CREATE TRIGGER %I_frozen BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cep_frozen_child()', t, t);
  END LOOP;
  CREATE TRIGGER estimate_versions_guard BEFORE UPDATE OR DELETE ON estimate_versions
    FOR EACH ROW EXECUTE FUNCTION cep_version_guard();
END $$;
"""
