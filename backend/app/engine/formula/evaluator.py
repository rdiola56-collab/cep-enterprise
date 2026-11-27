"""Formula definitions, dimensional validation, versioning and evaluation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Mapping

from ..precision import D, ENGINE_PRECISION
from ..units import DIMENSION_KINDS, DIMENSIONLESS, Dimension, Quantity, UnitError, check_unit_kind, get_unit
from .parser import BinOp, Call, FormulaSyntaxError, Node, Num, Pow, UnaryOp, Var, parse, render, variables


class FormulaDefinitionError(ValueError):
    """The formula definition is invalid (syntax, undeclared variable, wrong output dimension...)."""


class FormulaEvaluationError(ValueError):
    """The formula could not be evaluated with the given inputs."""


@dataclass(frozen=True)
class InputSpec:
    name: str
    kind: str  # key of DIMENSION_KINDS
    unit: str  # default unit for entry / display
    label: str = ""
    required: bool = True
    default: Decimal | None = None  # in `unit`
    min_value: Decimal | None = None  # in `unit`, inclusive
    max_value: Decimal | None = None  # in `unit`, inclusive

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "unit": self.unit,
            "label": self.label,
            "required": self.required,
            "default": None if self.default is None else str(self.default),
            "min_value": None if self.min_value is None else str(self.min_value),
            "max_value": None if self.max_value is None else str(self.max_value),
        }


@dataclass(frozen=True)
class FormulaDefinition:
    """One immutable version of a reusable estimating formula."""

    code: str
    name: str
    expression: str
    output_kind: str
    output_unit: str
    inputs: tuple[InputSpec, ...]
    version: int = 1
    description: str = ""
    _ast: Node | None = field(default=None, compare=False, repr=False)

    def canonical(self) -> dict:
        return {
            "code": self.code,
            "expression": render(self.ast),
            "output_kind": self.output_kind,
            "output_unit": get_unit(self.output_unit).symbol,
            "inputs": sorted((i.to_dict() for i in self.inputs), key=lambda d: d["name"]),
        }

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    @property
    def ast(self) -> Node:
        if self._ast is None:
            raise FormulaDefinitionError("formula not validated; call validate() first")
        return self._ast

    def validate(self) -> "FormulaDefinition":
        """Parse and dimension-check. Returns a validated copy carrying the AST."""
        try:
            tree = parse(self.expression)
        except FormulaSyntaxError as exc:
            raise FormulaDefinitionError(str(exc)) from exc
        names = [i.name for i in self.inputs]
        if len(set(names)) != len(names):
            raise FormulaDefinitionError("duplicate input names")
        specs = {i.name: i for i in self.inputs}
        undeclared = variables(tree) - specs.keys()
        if undeclared:
            raise FormulaDefinitionError(f"undeclared variables: {', '.join(sorted(undeclared))}")
        unused = specs.keys() - variables(tree)
        if unused:
            raise FormulaDefinitionError(f"declared inputs not used by the expression: {', '.join(sorted(unused))}")
        try:
            for spec in self.inputs:
                check_unit_kind(spec.unit, spec.kind)
            out_unit = check_unit_kind(self.output_unit, self.output_kind)
        except UnitError as exc:
            raise FormulaDefinitionError(str(exc)) from exc
        dims = {name: DIMENSION_KINDS[spec.kind] for name, spec in specs.items()}
        try:
            result_dim = infer_dimension(tree, dims)
        except UnitError as exc:
            raise FormulaDefinitionError(f"dimension error: {exc}") from exc
        if result_dim != out_unit.dimension:
            raise FormulaDefinitionError(
                f"expression produces {result_dim.describe()} but output is declared as {self.output_kind}"
            )
        object.__setattr__(self, "_ast", tree)
        return self


def infer_dimension(node: Node, dims: Mapping[str, Dimension]) -> Dimension:
    match node:
        case Num():
            return DIMENSIONLESS
        case Var(name):
            return dims[name]
        case UnaryOp(_, operand):
            return infer_dimension(operand, dims)
        case BinOp(op, left, right):
            l, r = infer_dimension(left, dims), infer_dimension(right, dims)
            if op in "+-":
                if l != r:
                    raise UnitError(f"cannot {'add' if op == '+' else 'subtract'} {r.describe()} and {l.describe()}")
                return l
            return l * r if op == "*" else l / r
        case Pow(base, exp):
            return infer_dimension(base, dims) ** exp
        case Call(func, args):
            arg_dims = [infer_dimension(a, dims) for a in args]
            if func in ("ceil", "floor"):
                if not arg_dims[0].is_dimensionless:
                    raise UnitError(f"{func}() needs a pure number (e.g. a count), got {arg_dims[0].describe()}")
                return DIMENSIONLESS
            if func == "round":
                if not all(d.is_dimensionless for d in arg_dims):
                    raise UnitError("round() needs pure numbers; convert to a count first")
                if len(args) == 2 and not isinstance(args[1], Num):
                    raise UnitError("round() places must be a literal number")
                return DIMENSIONLESS
            if func == "abs":
                return arg_dims[0]
            if func in ("min", "max"):
                if len(set(arg_dims)) != 1:
                    raise UnitError(f"{func}() arguments must share one dimension")
                return arg_dims[0]
            if func == "sqrt":
                return arg_dims[0].root(2)
    raise TypeError(node)


@dataclass(frozen=True)
class InputValue:
    name: str
    value: Decimal
    unit: str
    si: Decimal


@dataclass(frozen=True)
class FormulaResult:
    """Everything needed to audit one calculation."""

    formula_code: str
    formula_version: int
    formula_hash: str
    expression: str
    methodology: str  # expression with input values substituted
    inputs: tuple[InputValue, ...]
    value: Decimal
    unit: str

    def to_dict(self) -> dict:
        return {
            "formula_code": self.formula_code,
            "formula_version": self.formula_version,
            "formula_hash": self.formula_hash,
            "expression": self.expression,
            "methodology": self.methodology,
            "inputs": [
                {"name": i.name, "value": str(i.value), "unit": i.unit, "si": str(i.si)} for i in self.inputs
            ],
            "value": format(self.value.normalize(), "f"),
            "unit": self.unit,
        }


RawInput = Quantity | tuple[Decimal | int | str, str] | Decimal | int | str


def evaluate(formula: FormulaDefinition, raw_inputs: Mapping[str, RawInput]) -> FormulaResult:
    tree = formula.ast
    specs = {i.name: i for i in formula.inputs}
    unknown = raw_inputs.keys() - specs.keys()
    if unknown:
        raise FormulaEvaluationError(f"unknown inputs: {', '.join(sorted(unknown))}")

    resolved: dict[str, Quantity] = {}
    recorded: list[InputValue] = []
    for spec in formula.inputs:
        raw = raw_inputs.get(spec.name)
        if raw is None:
            if spec.default is None:
                if spec.required:
                    raise FormulaEvaluationError(f"missing input '{spec.name}'")
                raw = (Decimal(0), spec.unit)
            else:
                raw = (spec.default, spec.unit)
        try:
            value, unit, q = _coerce(raw, spec)
        except UnitError as exc:
            raise FormulaEvaluationError(f"input '{spec.name}': {exc}") from exc
        in_spec_unit = q.to(spec.unit)
        if spec.min_value is not None and in_spec_unit < spec.min_value:
            raise FormulaEvaluationError(f"input '{spec.name}' below minimum {spec.min_value} {spec.unit}")
        if spec.max_value is not None and in_spec_unit > spec.max_value:
            raise FormulaEvaluationError(f"input '{spec.name}' above maximum {spec.max_value} {spec.unit}")
        resolved[spec.name] = q
        recorded.append(InputValue(spec.name, value, unit, q.si))

    with localcontext() as ctx:
        ctx.prec = ENGINE_PRECISION
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        try:
            si_value = _eval(tree, {k: v.si for k, v in resolved.items()})
        except (DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
            raise FormulaEvaluationError(f"calculation error: {exc.__class__.__name__}") from exc
        out = get_unit(formula.output_unit)
        value = si_value / out.to_si

    substituted = _substitute(tree, {i.name: f"{i.value} {i.unit}".strip() for i in recorded})
    return FormulaResult(
        formula_code=formula.code,
        formula_version=formula.version,
        formula_hash=formula.content_hash,
        expression=render(tree),
        methodology=substituted,
        inputs=tuple(recorded),
        value=value,
        unit=out.symbol,
    )


def _coerce(raw: RawInput, spec: InputSpec) -> tuple[Decimal, str, Quantity]:
    expected = DIMENSION_KINDS[spec.kind]
    if isinstance(raw, Quantity):
        if raw.dimension != expected:
            raise UnitError(f"expected {spec.kind}, got {raw.dimension.describe()}")
        return raw.to(spec.unit), get_unit(spec.unit).symbol, raw
    if isinstance(raw, tuple):
        value, unit = raw
        u = get_unit(unit)
        if u.dimension != expected:
            raise UnitError(f"unit '{unit}' is {u.dimension.describe()}, expected {spec.kind}")
        return D(value), u.symbol, Quantity.of(value, u.symbol)
    return D(raw), get_unit(spec.unit).symbol, Quantity.of(raw, spec.unit)


def _eval(node: Node, env: Mapping[str, Decimal]) -> Decimal:
    match node:
        case Num(v):
            return v
        case Var(name):
            return env[name]
        case UnaryOp(_, operand):
            return -_eval(operand, env)
        case BinOp(op, left, right):
            l, r = _eval(left, env), _eval(right, env)
            if op == "+":
                return l + r
            if op == "-":
                return l - r
            if op == "*":
                return l * r
            if r == 0:
                raise ZeroDivisionError("division by zero")
            return l / r
        case Pow(base, exp):
            b = _eval(base, env)
            if exp < 0 and b == 0:
                raise ZeroDivisionError("zero to a negative power")
            return b**exp
        case Call(func, args):
            vals = [_eval(a, env) for a in args]
            if func == "ceil":
                return vals[0].to_integral_value(rounding=ROUND_CEILING)
            if func == "floor":
                return vals[0].to_integral_value(rounding=ROUND_FLOOR)
            if func == "round":
                places = int(vals[1]) if len(vals) == 2 else 0
                return vals[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
            if func == "abs":
                return abs(vals[0])
            if func == "min":
                return min(vals)
            if func == "max":
                return max(vals)
            if func == "sqrt":
                if vals[0] < 0:
                    raise InvalidOperation("square root of a negative number")
                return vals[0].sqrt()
    raise TypeError(node)


def _substitute(node: Node, values: Mapping[str, str]) -> str:
    match node:
        case Num(v):
            return format(v, "f")
        case Var(name):
            return f"[{values[name]}]"
        case UnaryOp(_, operand):
            return f"-{_sub_wrap(operand, values)}"
        case BinOp(op, left, right):
            sym = {"*": "×", "/": "÷", "-": "−", "+": "+"}[op]
            return f"{_sub_wrap(left, values)} {sym} {_sub_wrap(right, values)}"
        case Pow(base, exp):
            return f"{_sub_wrap(base, values)}^{exp}"
        case Call(func, args):
            return f"{func}({', '.join(_substitute(a, values) for a in args)})"
    raise TypeError(node)


def _sub_wrap(node: Node, values: Mapping[str, str]) -> str:
    text = _substitute(node, values)
    return f"({text})" if isinstance(node, (BinOp, UnaryOp)) else text
