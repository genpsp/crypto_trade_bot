"""Centralised marker tuples for classifying run_cycle SKIPPED / FAILED reasons.

Previously duplicated verbatim in run_cycle.py and slack_notifier.py. Keeping
them in one module avoids the two diverging silently.
"""

from __future__ import annotations

EXECUTION_ERROR_SKIP_MARKERS: tuple[str, ...] = (
    "insufficient funds",
    "slippage exceeded",
    "route/liquidity unavailable",
    "entry execution skipped",
    "exit slippage exceeded",
    "exit route/liquidity unavailable",
)

MARKET_DATA_MAINTENANCE_MARKERS: tuple[str, ...] = (
    "err-5201",
    "maintenance",
)

MARK_PRICE_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "err-5003",
    "requests are too many",
)
