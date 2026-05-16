"""Shared numeric helpers for gmo_bot.

Centralises constants and small float/Decimal helpers that were previously
duplicated across multiple modules.
"""

from __future__ import annotations

# Tolerance for size/PnL comparisons. Smaller than any meaningful position size
# but large enough to absorb IEEE-754 accumulation drift.
POSITION_SIZE_EPSILON = 1e-9


def decimal_str(value: float) -> str:
    """Format ``value`` for GMO API payloads (trim trailing zeros, no exponent)."""

    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"
