from __future__ import annotations

from typing import Protocol

from pybot.domain.model.types import OhlcvBar, Pair, SignalTimeframe


class MarketDataPort(Protocol):
    def fetch_bars(self, pair: Pair, timeframe: SignalTimeframe, limit: int) -> list[OhlcvBar]: ...

