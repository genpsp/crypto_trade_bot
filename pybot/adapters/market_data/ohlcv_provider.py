from __future__ import annotations

from datetime import UTC, datetime
import json

import requests
from redis import Redis

from pybot.app.ports.market_data_port import MarketDataPort
from pybot.domain.model.types import OhlcvBar, Pair, SignalTimeframe
from pybot.domain.utils.time import get_bar_duration_seconds

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_OHLCV_LIMIT = 1000
DEFAULT_OHLCV_CACHE_TTL_SECONDS = 30
PAIR_SYMBOL_MAP: dict[Pair, str] = {"SOL/USDC": "SOLUSDC"}
TIMEFRAME_TO_BINANCE_INTERVAL: dict[SignalTimeframe, str] = {"15m": "15m", "2h": "2h", "4h": "4h"}


class OhlcvProvider(MarketDataPort):
    def __init__(self, redis: Redis | None = None, cache_ttl_seconds: int = DEFAULT_OHLCV_CACHE_TTL_SECONDS):
        self.redis = redis
        self.cache_ttl_seconds = max(int(cache_ttl_seconds), 0)

    def _build_cache_key(self, symbol: str, interval: str, limit: int, end_time_ms: int | None) -> str:
        end_time_token = "latest" if end_time_ms is None else str(end_time_ms)
        return f"cache:ohlcv:{symbol}:{interval}:{limit}:{end_time_token}"

    def _get_cached_rows(self, cache_key: str) -> list[list] | None:
        if self.redis is None or self.cache_ttl_seconds <= 0:
            return None
        try:
            cached_payload = self.redis.get(cache_key)
        except Exception:
            return None
        if cached_payload is None:
            return None
        if isinstance(cached_payload, bytes):
            raw_payload = cached_payload.decode("utf-8")
        elif isinstance(cached_payload, str):
            raw_payload = cached_payload
        else:
            return None
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            return None
        return parsed if isinstance(parsed, list) else None

    def _set_cached_rows(self, cache_key: str, rows: list[list]) -> None:
        if self.redis is None or self.cache_ttl_seconds <= 0:
            return
        try:
            self.redis.set(cache_key, json.dumps(rows), ex=self.cache_ttl_seconds)
        except Exception:
            return

    def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
        end_time_ms: int | None = None,
    ) -> list[list]:
        cache_key = self._build_cache_key(symbol, interval, limit, end_time_ms)
        cached_rows = self._get_cached_rows(cache_key)
        if cached_rows is not None:
            return cached_rows

        params: dict[str, str] = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        if end_time_ms is not None:
            params["endTime"] = str(end_time_ms)

        response = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch OHLCV: HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("OHLCV payload is not an array")
        self._set_cached_rows(cache_key, payload)
        return payload

    def _rows_to_bars(self, rows: list[list], bar_duration_seconds: int) -> list[OhlcvBar]:
        bars: list[OhlcvBar] = []
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 6:
                raise RuntimeError(f"Invalid OHLCV row at index {index}")
            open_time_ms = int(row[0])
            open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC)
            close_time = datetime.fromtimestamp(
                (open_time_ms / 1000) + bar_duration_seconds,
                tz=UTC,
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

    def fetch_bars(self, pair: Pair, timeframe: SignalTimeframe, limit: int) -> list[OhlcvBar]:
        if limit <= 0 or limit > MAX_OHLCV_LIMIT:
            raise ValueError(f"OHLCV limit must be 1..{MAX_OHLCV_LIMIT}, got {limit}")

        symbol = PAIR_SYMBOL_MAP[pair]
        interval = TIMEFRAME_TO_BINANCE_INTERVAL[timeframe]
        bar_duration_seconds = get_bar_duration_seconds(timeframe)

        rows = self._fetch_klines(symbol=symbol, interval=interval, limit=limit)
        return self._rows_to_bars(rows=rows, bar_duration_seconds=bar_duration_seconds)

    def fetch_bars_backfill(
        self,
        pair: Pair,
        timeframe: SignalTimeframe,
        total_limit: int,
    ) -> list[OhlcvBar]:
        if total_limit <= 0:
            raise ValueError(f"OHLCV total_limit must be >= 1, got {total_limit}")

        symbol = PAIR_SYMBOL_MAP[pair]
        interval = TIMEFRAME_TO_BINANCE_INTERVAL[timeframe]
        bar_duration_seconds = get_bar_duration_seconds(timeframe)

        rows_by_open_ms: dict[int, list] = {}
        remaining = total_limit
        end_time_ms: int | None = None

        while remaining > 0:
            batch_limit = min(remaining, MAX_OHLCV_LIMIT)
            payload = self._fetch_klines(
                symbol=symbol,
                interval=interval,
                limit=batch_limit,
                end_time_ms=end_time_ms,
            )
            if len(payload) == 0:
                break

            for row in payload:
                if not isinstance(row, list) or len(row) < 6:
                    raise RuntimeError("Invalid OHLCV row returned by exchange")
                rows_by_open_ms[int(row[0])] = row

            oldest_open_ms = int(payload[0][0])
            end_time_ms = oldest_open_ms - 1
            remaining -= len(payload)

            if len(payload) < batch_limit:
                break

        sorted_rows = [rows_by_open_ms[key] for key in sorted(rows_by_open_ms.keys())]
        if len(sorted_rows) > total_limit:
            sorted_rows = sorted_rows[-total_limit:]
        return self._rows_to_bars(rows=sorted_rows, bar_duration_seconds=bar_duration_seconds)
