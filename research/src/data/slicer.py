from __future__ import annotations

from datetime import UTC, datetime

from apps.dex_bot.domain.model.types import OhlcvBar


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def slice_bars_by_close_time(bars: list[OhlcvBar], start: datetime, end: datetime) -> list[OhlcvBar]:
    start_utc = _to_utc(start)
    end_utc = _to_utc(end)
    return [bar for bar in bars if start_utc <= _to_utc(bar.close_time) <= end_utc]


def slice_bars_by_count(bars: list[OhlcvBar], end_index: int, count: int) -> list[OhlcvBar]:
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    resolved_end_index = end_index if end_index >= 0 else len(bars) + end_index
    if resolved_end_index < 0 or resolved_end_index >= len(bars):
        raise IndexError(f"end_index out of range: {end_index}")
    start_index = max(0, resolved_end_index - count + 1)
    return bars[start_index : resolved_end_index + 1]
