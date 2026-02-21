from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pybot.domain.model.types import OhlcvBar

OHLCV_FIELDNAMES = [
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def write_bars_to_csv(path: str | Path, bars: list[OhlcvBar]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OHLCV_FIELDNAMES)
        writer.writeheader()
        for bar in bars:
            writer.writerow(
                {
                    "open_time": _to_utc_iso(bar.open_time),
                    "close_time": _to_utc_iso(bar.close_time),
                    "open": f"{bar.open:.12f}",
                    "high": f"{bar.high:.12f}",
                    "low": f"{bar.low:.12f}",
                    "close": f"{bar.close:.12f}",
                    "volume": f"{bar.volume:.12f}",
                }
            )


def read_bars_from_csv(path: str | Path) -> list[OhlcvBar]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"OHLCV CSV not found: {source}")

    bars: list[OhlcvBar] = []
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            if row is None:
                continue

            try:
                bars.append(
                    OhlcvBar(
                        open_time=_parse_utc_iso(row["open_time"]),
                        close_time=_parse_utc_iso(row["close_time"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
            except Exception as error:
                raise ValueError(f"Invalid OHLCV row at index {index}: {row}") from error

    if not bars:
        raise ValueError(f"OHLCV CSV has no rows: {source}")

    bars.sort(key=lambda bar: bar.open_time)
    return bars


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
