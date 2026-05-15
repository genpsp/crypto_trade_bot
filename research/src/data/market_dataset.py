from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.partitioned_cache import PartitionedOhlcvCache


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def compute_data_hash(bars: list[OhlcvBar]) -> str:
    if not bars:
        payload = "empty"
    else:
        payload = f"{_iso(bars[0].close_time)}|{_iso(bars[-1].close_time)}|{len(bars)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DatasetKey:
    broker: str
    pair: str
    timeframe: str

    def to_dict(self) -> dict[str, str]:
        return {"broker": self.broker, "pair": self.pair, "timeframe": self.timeframe}

    def stable_key(self) -> str:
        return f"{self.broker}:{self.pair}:{self.timeframe}"


@dataclass(frozen=True)
class MarketDataset:
    broker: str
    pair: str
    timeframe: str
    start: datetime
    end: datetime
    bars: list[OhlcvBar]
    data_hash: str

    @property
    def key(self) -> DatasetKey:
        return DatasetKey(broker=self.broker, pair=self.pair, timeframe=self.timeframe)

    @classmethod
    def from_bars(
        cls,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        bars: list[OhlcvBar],
    ) -> "MarketDataset":
        sorted_bars = sorted(bars, key=lambda bar: bar.close_time)
        if sorted_bars:
            start = _to_utc(sorted_bars[0].close_time)
            end = _to_utc(sorted_bars[-1].close_time)
        else:
            now = datetime.now(tz=UTC)
            start = now
            end = now
        return cls(
            broker=broker,
            pair=pair,
            timeframe=timeframe,
            start=start,
            end=end,
            bars=sorted_bars,
            data_hash=compute_data_hash(sorted_bars),
        )

    @classmethod
    def load(
        cls,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        cache_root: str = "research/data/cache",
        start: datetime | None = None,
        end: datetime | None = None,
        last_n_days: float | None = None,
    ) -> "MarketDataset":
        resolved_end = _to_utc(end) if end is not None else None
        resolved_start = _to_utc(start) if start is not None else None
        if last_n_days is not None:
            if last_n_days <= 0:
                raise ValueError(f"last_n_days must be positive, got {last_n_days}")
            resolved_end = resolved_end or datetime.now(tz=UTC)
            resolved_start = resolved_end - timedelta(days=last_n_days)
        cache = PartitionedOhlcvCache(cache_root)
        bars = cache.load_bars(
            broker=broker,
            pair=pair,
            timeframe=timeframe,
            start=resolved_start,
            end=resolved_end,
        )
        return cls.from_bars(broker=broker, pair=pair, timeframe=timeframe, bars=bars)

    def slice(self, start: datetime, end: datetime) -> "MarketDataset":
        start_utc = _to_utc(start)
        end_utc = _to_utc(end)
        sliced = [bar for bar in self.bars if start_utc <= _to_utc(bar.close_time) <= end_utc]
        return MarketDataset.from_bars(
            broker=self.broker,
            pair=self.pair,
            timeframe=self.timeframe,
            bars=sliced,
        )

    def slice_by_bar_count(self, end_index: int, count: int) -> "MarketDataset":
        if count <= 0:
            raise ValueError(f"count must be positive, got {count}")
        resolved_end_index = end_index if end_index >= 0 else len(self.bars) + end_index
        if resolved_end_index < 0 or resolved_end_index >= len(self.bars):
            raise IndexError(f"end_index out of range: {end_index}")
        start_index = max(0, resolved_end_index - count + 1)
        return MarketDataset.from_bars(
            broker=self.broker,
            pair=self.pair,
            timeframe=self.timeframe,
            bars=self.bars[start_index : resolved_end_index + 1],
        )
