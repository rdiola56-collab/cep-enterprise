"""The Universal Estimating Engine facade.

Every estimating module, every API endpoint and every future drawing/BIM
import calls :meth:`EstimatingEngine.calculate_item`. It is the only path from
measured inputs to a priced estimate item:

    inputs → module validation → shared formula (versioned) → quantity
           → assembly explosion → material / labor / equipment → rate analysis
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Mapping

from ..modules.registry import ModuleDefinition, get_module
from .assembly import Assembly, MaterialComponent
from .costing import calculate_item
from .estimate import EstimateItem
from .formula.evaluator import FormulaDefinition, FormulaEvaluationError, RawInput, evaluate
from .rates import RateBook
from .units import Quantity

FormulaResolver = Callable[[str, int | None], FormulaDefinition]
AssemblyResolver = Callable[[str], Assembly]


class ValidationFailed(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class EstimatingEngine:
    formulas: FormulaResolver  # (code, version|None) -> definition
    assemblies: AssemblyResolver  # code -> the assembly in effect for this project
    rates: RateBook

    def calculate_item(
        self,
        *,
        item_id: str,
        module: str | ModuleDefinition,
        item_type: str,
        inputs: Mapping[str, RawInput],
        as_of: date,
        description: str | None = None,
        location: Mapping[str, str] | None = None,
        condition: str = "normal",
        assembly_code: str | None = None,
        formula_version: int | None = None,
        target_days: Decimal | None = None,
        priced_item_code: str | None = None,
    ) -> EstimateItem:
        mod = get_module(module) if isinstance(module, str) else module
        if mod.kind != "estimating" or mod.status != "configured":
            raise ValidationFailed([f"module '{mod.name}' does not take estimate items"])
        it = mod.item_type(item_type)
        formula = self.formulas(it.formula_code, formula_version)
        try:
            result = evaluate(formula, inputs)
        except FormulaEvaluationError as exc:
            raise ValidationFailed([str(exc)]) from exc

        errors = []
        by_name = {i.name: i for i in result.inputs}
        for rule in it.rules:
            iv = by_name.get(rule.input)
            if iv is None:
                continue
            spec_unit = next(s.unit for s in formula.inputs if s.name == rule.input)
            value = Quantity(iv.si, Quantity.of(0, spec_unit).dimension).to(spec_unit)
            if rule.min_value is not None and value < rule.min_value:
                errors.append(rule.message or f"{rule.input} below {rule.min_value}")
            if rule.max_value is not None and value > rule.max_value:
                errors.append(rule.message or f"{rule.input} above {rule.max_value}")
        if errors:
            raise ValidationFailed(errors)

        if it.priced_directly:
            if not priced_item_code:
                raise ValidationFailed([f"{it.label} needs a price-book item (priced_item_code)"])
            if assembly_code:
                raise ValidationFailed([f"{it.label} is priced directly, not through an assembly"])
            assembly = direct_price_assembly(priced_item_code, result.unit, mod.trade, it.label)
        else:
            if priced_item_code:
                raise ValidationFailed([f"{it.label} is priced through an assembly, not directly"])
            assembly = self.assemblies(assembly_code or it.default_assembly)
        work_qty = Quantity.of(result.value, result.unit)
        breakdown = calculate_item(assembly, work_qty, self.rates, as_of, condition, target_days)
        return EstimateItem(
            item_id=item_id,
            module=mod.code,
            trade=mod.trade,
            description=description or it.label,
            assembly_code=assembly.code,
            priced_item_code=priced_item_code,
            breakdown=breakdown,
            location=dict(location or {}),
            quantity_source=result,
        )


DIRECT_PREFIX = "PRICED:"


def direct_price_assembly(item_code: str, unit: str, trade: str, label: str) -> Assembly:
    """One-line assembly for items priced straight from the price book (e.g. provisional sums).
    The amount still comes from a dated, versioned price record and still passes through rate analysis."""
    return Assembly(
        code=f"{DIRECT_PREFIX}{item_code}",
        description=label,
        trade=trade,
        unit=unit,
        materials=(MaterialComponent(item_code, label, Decimal(1), unit, category="other"),),
    ).validate()
