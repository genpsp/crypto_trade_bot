"""Pair-to-GMO-symbol mapping.

Previously duplicated in adapters/execution/gmo_margin_execution.py and
adapters/market_data/ohlcv_provider.py. Adding a new pair (e.g. BTC/JPY) only
requires updating this single dict.
"""

from __future__ import annotations

from apps.gmo_bot.domain.model.types import Pair

PAIR_SYMBOL_MAP: dict[Pair, str] = {
    "SOL/JPY": "SOL_JPY",
    "BTC/JPY": "BTC_JPY",
    "ETH/JPY": "ETH_JPY",
}
