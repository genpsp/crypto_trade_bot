"""Shared coercion helpers consolidating duplicated implementations.

Previously each of bootstrap.py / close_position.py / gmo_margin_execution.py /
exit_order_monitor.py / protective_exit_orders.py / daily_trade_summary.py kept
its own slightly-different copy of these helpers. Drift between them was a
silent maintenance hazard.
"""

from __future__ import annotations

from typing import Any


def to_float(value: Any) -> float | None:
    """Best-effort cast to ``float`` (``None`` on failure or ``bool``)."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def to_str(value: Any) -> str | None:
    """Best-effort cast to ``str`` (``None`` on empty/whitespace)."""

    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if value is None:
        return None
    return str(value)


def as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` if it is a ``dict``, otherwise an empty ``dict``."""

    if isinstance(value, dict):
        return value
    return {}
