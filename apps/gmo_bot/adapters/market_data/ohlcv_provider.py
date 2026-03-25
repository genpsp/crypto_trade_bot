from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import json
from typing import Any, Callable

from redis import Redis
import requests

from apps.dex_bot.domain.model.types import OhlcvBar
from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.app.ports.market_data_port import MarketDataPort
from apps.gmo_bot.domain.model.types import Pair, SignalTimeframe

PAIR_SYMBOL_MAP: dict[Pair, str] = {"SOL/JPY": "SOL_JPY"}
TIMEFRAME_TO_GMO_INTERVAL: dict[SignalTimeframe, str] = {
    "15m": "15min",
    "2h": "1hour",
    "4h": "4hour",
}
DEFAULT_OHLCV_CACHE_TTL_SECONDS = 30
JST = timezone(timedelta(hours=9))


class OhlcvProvider(MarketDataPort):
    def __init__(
        self,
        client: GmoApiClient,
        redis: Redis | None = None,
        cache_ttl_seconds: int = DEFAULT_OHLCV_CACHE_TTL_SECONDS,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.redis = redis
        self.cache_ttl_seconds = max(int(cache_ttl_seconds), 0)
        self.now_provider = now_provider

    def fetch_bars(self, pair: Pair, timeframe: SignalTimeframe, limit: int) -> list[OhlcvBar]:
        if limit <= 0:
            raise ValueError("limit must be > 0")
        symbol = PAIR_SYMBOL_MAP[pair]
        source_interval = TIMEFRAME_TO_GMO_INTERVAL[timeframe]
        source_limit = limit * 2 if timeframe == "2h" else limit
        source_bars = self._fetch_recent_source_bars(symbol, source_interval, source_limit)
        if timeframe == "2h":
            source_bars = self._aggregate_bars(source_bars, bucket_seconds=2 * 60 * 60)
        return source_bars[-limit:]

    def fetch_bars_backfill(
        self,
        pair: Pair,
        timeframe: SignalTimeframe,
        total_limit: int,
    ) -> list[OhlcvBar]:
        return self.fetch_bars(pair=pair, timeframe=timeframe, limit=total_limit)

    def _fetch_recent_source_bars(self, symbol: str, interval: str, limit: int) -> list[OhlcvBar]:
        bars_by_open_time: dict[datetime, OhlcvBar] = {}
        fetched_at = self._now()
        cursor = fetched_at
        attempts = 0
        max_attempts = self._max_attempts_for_interval(interval, limit)
        while len(bars_by_open_time) < limit and attempts < max_attempts:
            date_token = self._date_token(cursor, interval)
            cache_key = f"cache:gmo:ohlcv:{symbol}:{interval}:{date_token}"
            rows = self._get_cached_rows(cache_key)
            if rows is None:
                try:
                    rows = self.client.get_klines(symbol=symbol, interval=interval, date=date_token)
                except requests.HTTPError as error:
                    if self._should_fallback_previous_date_token(error, cursor, fetched_at, interval):
                        cursor = self._step_cursor(cursor, interval)
                        attempts += 1
                        continue
                    raise
                self._set_cached_rows(cache_key, rows)
            for row in rows:
                bar = self._row_to_bar(row, interval)
                if bar is not None and self._is_confirmed_bar(bar, fetched_at):
                    bars_by_open_time[bar.open_time] = bar
            cursor = self._step_cursor(cursor, interval)
            attempts += 1
        bars = [bars_by_open_time[key] for key in sorted(bars_by_open_time.keys())]
        return bars[-limit:]

    def _get_cached_rows(self, key: str) -> list[dict[str, Any]] | None:
        if self.redis is None or self.cache_ttl_seconds <= 0:
            return None
        try:
            payload = self.redis.get(key)
        except Exception:
            return None
        if payload is None:
            return None
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return None

    def _set_cached_rows(self, key: str, rows: list[dict[str, Any]]) -> None:
        if self.redis is None or self.cache_ttl_seconds <= 0:
            return
        try:
            self.redis.set(key, json.dumps(rows), ex=self.cache_ttl_seconds)
        except Exception:
            return

    def _date_token(self, cursor: datetime, interval: str) -> str:
        if interval == "4hour":
            return cursor.astimezone(UTC).strftime("%Y")
        return (cursor.astimezone(JST) - timedelta(hours=6)).strftime("%Y%m%d")

    def _step_cursor(self, cursor: datetime, interval: str) -> datetime:
        if interval == "4hour":
            return cursor.replace(year=cursor.year - 1)
        return cursor - timedelta(days=1)

    def _max_attempts_for_interval(self, interval: str, limit: int) -> int:
        if interval == "4hour":
            bars_per_token = 365 * 6
            return max(4, ((limit - 1) // bars_per_token) + 3)
        if interval == "1hour":
            bars_per_token = 24
        elif interval == "15min":
            bars_per_token = 96
        else:
            raise ValueError(f"unsupported GMO interval: {interval}")
        if limit <= 1_000:
            return max(40, ((limit - 1) // bars_per_token) + 10)
        return max(400, ((limit - 1) // bars_per_token) + 10)

    def _should_fallback_previous_date_token(
        self,
        error: requests.HTTPError,
        cursor: datetime,
        fetched_at: datetime,
        interval: str,
    ) -> bool:
        if interval == "4hour":
            return False
        response = getattr(error, "response", None)
        if response is None or response.status_code != 404:
            return False
        # GMO's intraday klines switch at JST 06:00, but the new day's bucket
        # can briefly lag right after the boundary. In that case the previous
        # date token still contains the latest confirmed bar.
        return self._date_token(cursor, interval) == self._date_token(fetched_at, interval)

    def _now(self) -> datetime:
        current = self.now_provider() if self.now_provider else datetime.now(tz=UTC)
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC)
        return current.astimezone(UTC)

    def _is_confirmed_bar(self, bar: OhlcvBar, fetched_at: datetime) -> bool:
        return bar.close_time <= fetched_at

    def _row_to_bar(self, row: dict[str, Any], interval: str) -> OhlcvBar | None:
        open_time_ms = row.get("openTime") or row.get("open_time")
        if not isinstance(open_time_ms, (str, int, float)):
            return None
        try:
            raw_open_time_ms = int(open_time_ms)
        except (TypeError, ValueError):
            return None
        duration = self._interval_seconds(interval)
        open_time, close_time = self._normalize_bar_window(raw_open_time_ms, duration)
        return OhlcvBar(
            open_time=open_time,
            close_time=close_time,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0)),
        )

    def _normalize_bar_window(self, raw_open_time_ms: int, duration_seconds: int) -> tuple[datetime, datetime]:
        duration_ms = duration_seconds * 1000
        normalized_close_time_ms = self._normalize_timestamp_ms(raw_open_time_ms + duration_ms, duration_ms)
        close_time = datetime.fromtimestamp(normalized_close_time_ms / 1000, tz=UTC)
        open_time = close_time - timedelta(seconds=duration_seconds)
        return open_time, close_time

    def _normalize_timestamp_ms(self, timestamp_ms: int, interval_ms: int) -> int:
        return ((timestamp_ms + (interval_ms // 2)) // interval_ms) * interval_ms

    def _interval_seconds(self, interval: str) -> int:
        if interval == "15min":
            return 15 * 60
        if interval == "1hour":
            return 60 * 60
        if interval == "4hour":
            return 4 * 60 * 60
        raise ValueError(f"unsupported GMO interval: {interval}")

    def _aggregate_bars(self, bars: list[OhlcvBar], bucket_seconds: int) -> list[OhlcvBar]:
        grouped: dict[int, list[OhlcvBar]] = {}
        for bar in bars:
            bucket = int(bar.open_time.timestamp()) // bucket_seconds
            grouped.setdefault(bucket, []).append(bar)
        aggregated: list[OhlcvBar] = []
        for bucket in sorted(grouped.keys()):
            items = sorted(grouped[bucket], key=lambda item: item.open_time)
            if len(items) < 2:
                continue
            aggregated.append(
                OhlcvBar(
                    open_time=items[0].open_time,
                    close_time=items[-1].close_time,
                    open=items[0].open,
                    high=max(item.high for item in items),
                    low=min(item.low for item in items),
                    close=items[-1].close,
                    volume=sum(item.volume for item in items),
                )
            )
        return aggregated
