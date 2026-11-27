from dataclasses import replace
from decimal import Decimal as Dec

import pytest

from app.engine.formula import (
    FormulaDefinition,
    FormulaDefinitionError,
    FormulaEvaluationError,
    FormulaLibrary,
    FormulaSyntaxError,
    InputSpec,
    evaluate,
    parse,
)
from app.engine.formula.parser import render

from .fixtures import formula_library


def F(expr, inputs, kind="dimensionless", unit="1", code="T"):
    return FormulaDefinition(code, code, expr, kind, unit, tuple(inputs)).validate()


def num(name):
    return InputSpec(name, "dimensionless", "1")


def length(name):
    return InputSpec(name, "length", "m")


# ------------------------------------------------------------------ parser safety


@pytest.mark.parametrize(
    "src",
    [
        "__import__('os').system('ls')",
        "a.b",
        "exec(1)",
        "open('x')",
        "a[0]",
        "lambda: 1",
        "'text'",
        "a ** 2",
        "a ^ 1.5",
        "a ^ b",
        "1 +",
        "()",
        "",
        "a; b",
        "a = 1",
    ],
)
def test_rejects_anything_outside_the_grammar(src):
    with pytest.raises(FormulaSyntaxError):
        parse(src)


def test_depth_and_size_limits():
    with pytest.raises(FormulaSyntaxError):
        parse("(" * 100 + "1" + ")" * 100)
    with pytest.raises(FormulaSyntaxError):
        parse("+".join(["a"] * 600))


def test_precedence_and_typographic_operators():
    assert render(parse("a + b × c")) == "a + (b * c)"
    assert render(parse("(a − b) ÷ c")) == "(a - b) / c"
    assert evaluate(F("-a^2", [num("a")]), {"a": 3}).value == -9  # power binds tighter than unary minus
    f = F("a + b * c ^ 2", [num("a"), num("b"), num("c")])
    assert evaluate(f, {"a": 1, "b": 2, "c": 3}).value == 19


# ------------------------------------------------------------------ validation


def test_undeclared_and_unused_variables():
    with pytest.raises(FormulaDefinitionError, match="undeclared"):
        F("a * b", [num("a")])
    with pytest.raises(FormulaDefinitionError, match="not used"):
        F("a", [num("a"), num("b")])


def test_dimension_checking():
    with pytest.raises(FormulaDefinitionError, match="dimension"):
        F("l + w", [length("l"), InputSpec("w", "area", "m2")], "length", "m")
    with pytest.raises(FormulaDefinitionError, match="produces area"):
        F("l * w", [length("l"), length("w")], "volume", "m3")
    with pytest.raises(FormulaDefinitionError, match="pure number"):
        F("ceil(l)", [length("l")], "length", "m")
    assert F("ceil(l / s) + 1", [length("l"), length("s")]).ast is not None


# ------------------------------------------------------------------ evaluation


def test_reference_formulas_with_mixed_units():
    lib = formula_library()
    r = evaluate(lib.get("CONCRETE_VOLUME"), {"length": (6, "m"), "width": (300, "mm"), "height": (500, "mm"), "pieces": 4})
    assert r.value == Dec("3.6") and r.unit == "m3"
    assert r.methodology == "(([6 m] × [300 mm]) × [500 mm]) × [4 pcs]"

    r = evaluate(lib.get("FLOOR_AREA"), {"length": (20, "ft"), "width": (10, "ft")})
    assert r.value == Dec("18.58060800")  # 200 ft² exactly

    r = evaluate(lib.get("WALL_AREA"), {"length": 10, "height": 3, "openings": (Dec("4.5"), "m2")})
    assert r.value == Dec("25.5")

    r = evaluate(lib.get("STEEL_WEIGHT"), {"section_weight": (Dec("29.8"), "kg/m"), "length": 12})
    assert r.value == Dec("357.6")

    r = evaluate(lib.get("TILE_QUANTITY"), {"floor_area": 36, "tile_coverage": (Dec("0.36"), "m2")})
    assert r.value == 100

    bars = evaluate(lib.get("REBAR_BAR_COUNT"), {"span": 6, "spacing": (200, "mm")})
    assert bars.value == 31
    r = evaluate(lib.get("REBAR_WEIGHT"), {"bars": bars.value, "cutting_length": Dec("3.2"), "unit_weight": Dec("0.888")})
    assert r.value == Dec("88.0896")


def test_imperial_output():
    f = FormulaDefinition("A", "a", "l*w", "area", "ft2", (length("l"), length("w"))).validate()
    assert evaluate(f, {"l": (Dec("3.048"), "m"), "w": (Dec("3.048"), "m")}).value == 100


def test_evaluation_errors():
    lib = formula_library()
    with pytest.raises(FormulaEvaluationError, match="missing"):
        evaluate(lib.get("FLOOR_AREA"), {"length": 1})
    with pytest.raises(FormulaEvaluationError, match="below minimum"):
        evaluate(lib.get("FLOOR_AREA"), {"length": -1, "width": 1})
    with pytest.raises(FormulaEvaluationError, match="calculation error"):
        evaluate(lib.get("TILE_QUANTITY"), {"floor_area": 1, "tile_coverage": 0})
    with pytest.raises(FormulaEvaluationError, match="expected length"):
        evaluate(lib.get("FLOOR_AREA"), {"length": (1, "kg"), "width": 1})
    with pytest.raises(FormulaEvaluationError, match="unknown inputs"):
        evaluate(lib.get("FLOOR_AREA"), {"length": 1, "width": 1, "x": 1})


def test_defaults_applied_and_recorded():
    r = evaluate(formula_library().get("FLOOR_AREA"), {"length": 5, "width": 4})
    assert r.value == 20
    assert {i.name: i.value for i in r.inputs}["repetitions"] == 1


# ------------------------------------------------------------------ versioning


def test_versioning_keeps_old_versions_reproducible():
    lib = FormulaLibrary()
    v1 = lib.publish(FormulaDefinition("WASTE", "w", "a * 1.05", "area", "m2", (InputSpec("a", "area", "m2"),)))
    v2 = lib.publish(replace(v1, expression="a * 1.10"))
    assert (v1.version, v2.version) == (1, 2)
    assert v1.content_hash != v2.content_hash
    assert evaluate(lib.get("WASTE", 1), {"a": 100}).value == Dec("105.00")
    assert evaluate(lib.get("WASTE"), {"a": 100}).value == Dec("110.00")
    res = evaluate(lib.get("WASTE", 1), {"a": 100})
    assert (res.formula_version, res.formula_hash) == (1, v1.content_hash)


def test_publishing_an_unchanged_formula_is_rejected():
    lib = FormulaLibrary()
    f = FormulaDefinition("X", "x", "a", "area", "m2", (InputSpec("a", "area", "m2"),))
    lib.publish(f)
    with pytest.raises(FormulaDefinitionError, match="unchanged"):
        lib.publish(replace(f, expression=" a "))
