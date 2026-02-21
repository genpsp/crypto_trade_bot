from __future__ import annotations

from datetime import UTC, datetime

import requests

from pybot.app.ports.market_data_port import MarketDataPort
from pybot.domain.model.types import OhlcvBar, Pair, SignalTimeframe
from pybot.domain.utils.time import get_bar_duration_seconds

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
PAIR_SYMBOL_MAP: dict[Pair, str] = {"SOL/USDC": "SOLUSDC"}
TIMEFRAME_TO_BINANCE_INTERVAL: dict[SignalTimeframe, str] = {"2h": "2h", "4h": "4h"}


class OhlcvProvider(MarketDataPort):
    def fetch_bars(self, pair: Pair, timeframe: SignalTimeframe, limit: int) -> list[OhlcvBar]:
        if limit <= 0 or limit > 1000:
            raise ValueError(f"OHLCV limit must be 1..1000, got {limit}")

        symbol = PAIR_SYMBOL_MAP[pair]
        interval = TIMEFRAME_TO_BINANCE_INTERVAL[timeframe]
        bar_duration_seconds = get_bar_duration_seconds(timeframe)
        response = requests.get(
            BINANCE_KLINES_URL,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch OHLCV: HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("OHLCV payload is not an array")

        bars: list[OhlcvBar] = []
        for index, row in enumerate(payload):
            if not isinstance(row, list) or len(row) < 6:
                raise RuntimeError(f"Invalid OHLCV row at index {index}")
            open_time_ms = int(row[0])
            open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC)
            close_time = datetime.fromtimestamp(
                (open_time_ms / 1000) + bar_duration_seconds, tz=UTC
            )
            bars.append(
                OhlcvBar(
                    open_time=open_time,
                    close_time=close_time,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return bars

