from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
import json
import re
from typing import Any, Iterable

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.source_registry import OhlcvProviderProtocol, fetch_recent_bars

_PAIR_SAFE_RE = re.compile(r"[^a-z0-9]+")


def is_pyarrow_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except Exception:
        return False
    return True


def _require_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as error:
        raise RuntimeError(
            "Parquet cache requires pyarrow. Install dependencies with `pip install -r requirements.txt`."
        ) from error
    return pa, pq


def pair_to_safe(pair: str) -> str:
    token = _PAIR_SAFE_RE.sub("", pair.lower())
    if not token:
        raise ValueError(f"pair cannot be converted to safe token: {pair!r}")
    return token


def broker_to_safe(broker: str) -> str:
    if broker == "GMO_COIN":
        return "gmo"
    if broker == "DEX":
        return "dex"
    token = _PAIR_SAFE_RE.sub("", broker.lower())
    if not token:
        raise ValueError(f"broker cannot be converted to safe token: {broker!r}")
    return token


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    if timeframe.endswith("m"):
        minutes = int(timeframe[:-1])
        return timedelta(minutes=minutes)
    if timeframe.endswith("h"):
        hours = int(timeframe[:-1])
        return timedelta(hours=hours)
    raise ValueError(f"unsupported timeframe: {timeframe}")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_utc_iso(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _month_key(value: datetime) -> str:
    return _to_utc(value).strftime("%Y-%m")


def _bar_to_row(bar: OhlcvBar, *, source: str, fetched_at: datetime) -> dict[str, Any]:
    return {
        "open_time": _to_utc_iso(bar.open_time),
        "close_time": _to_utc_iso(bar.close_time),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
        "source": source,
        "fetched_at": _to_utc_iso(fetched_at),
    }


def _row_to_bar(row: dict[str, Any]) -> OhlcvBar:
    return OhlcvBar(
        open_time=_parse_utc_iso(str(row["open_time"])),
        close_time=_parse_utc_iso(str(row["close_time"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


def _dedupe_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_close_time: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_close_time[str(row["close_time"])] = dict(row)
    return [by_close_time[key] for key in sorted(by_close_time.keys())]


def detect_gaps(bars: list[OhlcvBar], timeframe: str) -> list[dict[str, Any]]:
    if len(bars) < 2:
        return []
    expected_seconds = int(timeframe_to_timedelta(timeframe).total_seconds())
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(bars, bars[1:]):
        delta_seconds = int((_to_utc(current.close_time) - _to_utc(previous.close_time)).total_seconds())
        if delta_seconds <= 0:
            continue
        if delta_seconds != expected_seconds:
            missing_bars = max((delta_seconds // expected_seconds) - 1, 0)
            gaps.append(
                {
                    "after_close_time": _to_utc_iso(previous.close_time),
                    "before_close_time": _to_utc_iso(current.close_time),
                    "delta_seconds": delta_seconds,
                    "expected_seconds": expected_seconds,
                    "missing_bars": missing_bars,
                }
            )
    return gaps


@dataclass(frozen=True)
class CacheSyncResult:
    broker: str
    pair: str
    timeframe: str
    fetched_bars: int
    cached_bars: int
    first_close_time: str | None
    last_close_time: str | None
    manifest_path: Path
    gaps: list[dict[str, Any]]


class PartitionedOhlcvCache:
    def __init__(self, root: str | Path = "research/data/cache"):
        self.root = Path(root)

    def dataset_dir(self, *, broker: str, pair: str, timeframe: str) -> Path:
        return self.root / broker_to_safe(broker) / pair_to_safe(pair) / timeframe

    def manifest_path(self, *, broker: str, pair: str, timeframe: str) -> Path:
        return self.dataset_dir(broker=broker, pair=pair, timeframe=timeframe) / "_manifest.json"

    def partition_path(self, *, broker: str, pair: str, timeframe: str, month: str) -> Path:
        return self.dataset_dir(broker=broker, pair=pair, timeframe=timeframe) / f"{month}.parquet"

    def list_partition_paths(self, *, broker: str, pair: str, timeframe: str) -> list[Path]:
        base = self.dataset_dir(broker=broker, pair=pair, timeframe=timeframe)
        if not base.exists():
            return []
        return sorted(path for path in base.glob("*.parquet") if path.is_file())

    def read_rows_from_partition(self, path: str | Path) -> list[dict[str, Any]]:
        _, pq = _require_pyarrow()
        source = Path(path)
        if not source.exists():
            return []
        return [dict(row) for row in pq.read_table(source).to_pylist()]

    def write_rows_to_partition(self, path: str | Path, rows: list[dict[str, Any]]) -> None:
        pa, pq = _require_pyarrow()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, target, compression="zstd")

    def load_rows(
        self,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        start_utc = _to_utc(start) if start is not None else None
        end_utc = _to_utc(end) if end is not None else None
        rows: list[dict[str, Any]] = []
        for path in self.list_partition_paths(broker=broker, pair=pair, timeframe=timeframe):
            for row in self.read_rows_from_partition(path):
                close_time = _parse_utc_iso(str(row["close_time"]))
                if start_utc is not None and close_time < start_utc:
                    continue
                if end_utc is not None and close_time > end_utc:
                    continue
                rows.append(row)
        return _dedupe_rows(rows)

    def load_bars(
        self,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[OhlcvBar]:
        return [
            _row_to_bar(row)
            for row in self.load_rows(
                broker=broker,
                pair=pair,
                timeframe=timeframe,
                start=start,
                end=end,
            )
        ]

    def latest_close_time(self, *, broker: str, pair: str, timeframe: str) -> datetime | None:
        latest: datetime | None = None
        for path in self.list_partition_paths(broker=broker, pair=pair, timeframe=timeframe):
            for row in self.read_rows_from_partition(path):
                close_time = _parse_utc_iso(str(row["close_time"]))
                if latest is None or close_time > latest:
                    latest = close_time
        return latest

    def append_bars(
        self,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        bars: list[OhlcvBar],
        source: str,
        fetched_at: datetime | None = None,
    ) -> dict[str, Any]:
        if not bars:
            return self.update_manifest(broker=broker, pair=pair, timeframe=timeframe)
        resolved_fetched_at = _to_utc(fetched_at or datetime.now(tz=UTC))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for bar in bars:
            grouped.setdefault(_month_key(bar.close_time), []).append(
                _bar_to_row(bar, source=source, fetched_at=resolved_fetched_at)
            )
        for month, new_rows in grouped.items():
            path = self.partition_path(broker=broker, pair=pair, timeframe=timeframe, month=month)
            existing_rows = self.read_rows_from_partition(path) if path.exists() else []
            self.write_rows_to_partition(path, _dedupe_rows([*existing_rows, *new_rows]))
        return self.update_manifest(broker=broker, pair=pair, timeframe=timeframe, synced_at=resolved_fetched_at)

    def update_manifest(
        self,
        *,
        broker: str,
        pair: str,
        timeframe: str,
        synced_at: datetime | None = None,
    ) -> dict[str, Any]:
        bars = self.load_bars(broker=broker, pair=pair, timeframe=timeframe)
        gaps = detect_gaps(bars, timeframe)
        manifest = {
            "broker": broker,
            "pair": pair,
            "pair_safe": pair_to_safe(pair),
            "timeframe": timeframe,
            "storage_format": "parquet",
            "last_synced_at": _to_utc_iso(synced_at or datetime.now(tz=UTC)),
            "bar_count": len(bars),
            "first_close_time": _to_utc_iso(bars[0].close_time) if bars else None,
            "last_close_time": _to_utc_iso(bars[-1].close_time) if bars else None,
            "gaps": gaps,
        }
        target = self.manifest_path(broker=broker, pair=pair, timeframe=timeframe)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


def _estimate_fetch_limit(*, start: datetime | None, now: datetime, timeframe: str, fallback_limit: int) -> int:
    if start is None:
        return fallback_limit
    delta_seconds = max(0, int((_to_utc(now) - _to_utc(start)).total_seconds()))
    bar_seconds = int(timeframe_to_timedelta(timeframe).total_seconds())
    return max(2, ceil(delta_seconds / bar_seconds) + 2)


def sync_ohlcv_cache(
    *,
    cache: PartitionedOhlcvCache,
    provider: OhlcvProviderProtocol,
    broker: str,
    pair: str,
    timeframe: str,
    since: datetime | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> CacheSyncResult:
    resolved_now = _to_utc(now or datetime.now(tz=UTC))
    latest_close_time = cache.latest_close_time(broker=broker, pair=pair, timeframe=timeframe)
    start = latest_close_time or since
    fetch_limit = limit if limit is not None else _estimate_fetch_limit(
        start=start,
        now=resolved_now,
        timeframe=timeframe,
        fallback_limit=1000,
    )
    fetched_bars = fetch_recent_bars(provider, pair=pair, timeframe=timeframe, limit=fetch_limit)
    if since is not None and latest_close_time is None:
        since_utc = _to_utc(since)
        fetched_bars = [bar for bar in fetched_bars if _to_utc(bar.close_time) >= since_utc]
    manifest = cache.append_bars(
        broker=broker,
        pair=pair,
        timeframe=timeframe,
        bars=fetched_bars,
        source=broker.lower(),
        fetched_at=resolved_now,
    )
    return CacheSyncResult(
        broker=broker,
        pair=pair,
        timeframe=timeframe,
        fetched_bars=len(fetched_bars),
        cached_bars=int(manifest["bar_count"]),
        first_close_time=manifest["first_close_time"],
        last_close_time=manifest["last_close_time"],
        manifest_path=cache.manifest_path(broker=broker, pair=pair, timeframe=timeframe),
        gaps=list(manifest["gaps"]),
    )
