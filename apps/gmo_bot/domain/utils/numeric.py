"""Shared numeric helpers for gmo_bot.

Centralises constants and small float/Decimal helpers that were previously
duplicated across multiple modules.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# Tolerance for size/PnL comparisons. Smaller than any meaningful position size
# but large enough to absorb IEEE-754 accumulation drift.
POSITION_SIZE_EPSILON = 1e-9


def decimal_str(value: float) -> str:
    """Format ``value`` for GMO API payloads (trim trailing zeros, no exponent)."""

    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


# §4.1: JPY-aware helpers. GMO settles JPY in whole yen, so float
# accumulation of fees / pnl across many trades drifts (e.g. summing 0.1 ten
# times yields 0.9999...). Use Decimal when summing/diffing JPY amounts and
# round at persistence boundaries.

JPY_QUANTIZE = Decimal("1")  # whole yen


def to_decimal(value: float | int | str | Decimal) -> Decimal:
    """Coerce numeric value to Decimal via ``str()`` to avoid float repr drift."""

    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def sum_jpy(values: list[float | int | Decimal]) -> Decimal:
    """Sum a list of JPY amounts in Decimal space."""

    total = Decimal("0")
    for value in values:
        total += to_decimal(value)
    return total


def round_jpy(value: float | int | Decimal) -> float:
    """Round to whole yen and return as float for storage/Slack display."""

    quantized = to_decimal(value).quantize(JPY_QUANTIZE, rounding=ROUND_HALF_UP)
    return float(quantized)
