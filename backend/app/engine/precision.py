"""Numeric precision policy.

The engine computes with Decimal at 34 significant digits and never uses
floats. Rounding happens only at presentation/reporting boundaries, using the
project's configured money and quantity decimal places (ROUND_HALF_UP).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ENGINE_PRECISION = 34


def D(value: Decimal | int | str) -> Decimal:
    """Convert to Decimal without ever going through binary float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("booleans are not quantities")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip().replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {value!r}") from exc
    if isinstance(value, float):
        # Accept floats from JSON clients, but via their shortest repr, not binary expansion.
        return Decimal(repr(value))
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def quantize(value: Decimal, places: int) -> Decimal:
    exp = Decimal(1).scaleb(-places)
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def money(value: Decimal, places: int = 2) -> Decimal:
    return quantize(value, places)
