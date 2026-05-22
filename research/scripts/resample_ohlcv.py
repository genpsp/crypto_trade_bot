"""Resample 15m OHLCV CSV into longer-timeframe CSVs (1h / 4h / 1d).

Reads a `read_bars_from_csv` compatible CSV (15m bars) and aggregates into
hourly / 4-hour / daily buckets using standard OHLCV rules:

- open: first bar's open
- high: max of highs
- low: min of lows
- close: last bar's close
- volume: sum of volumes

Bars whose bucket is incomplete (fewer than expected sub-bars) at the tail are
dropped — this matches what a LIVE bar-confirmed flow would emit.

Usage:

    python -m research.scripts.resample_ohlcv \\
        --input research/data/raw/soljpy_15m_to_2026_05.csv \\
        --timeframe 1h \\
        --output research/data/raw/soljpy_1h_to_2026_05.csv
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_bars_to_csv

TIMEFRAME_TO_MINUTES = {"1h": 60, "4h": 240, "1d": 1440}


def _bucket_start(open_time: datetime, bucket_minutes: int) -> datetime:
    epoch_minutes = int(open_time.timestamp()) // 60
    bucket_epoch_minutes = (epoch_minutes // bucket_minutes) * bucket_minutes
    return datetime.fromtimestamp(bucket_epoch_minutes * 60, tz=open_time.tzinfo)


def resample(
    bars: list[OhlcvBar],
    target_timeframe: str,
    source_timeframe_minutes: int = 15,
) -> list[OhlcvBar]:
    if target_timeframe not in TIMEFRAME_TO_MINUTES:
        raise ValueError(
            f"unsupported target timeframe: {target_timeframe} "
            f"(allowed: {sorted(TIMEFRAME_TO_MINUTES)})"
        )
    bucket_minutes = TIMEFRAME_TO_MINUTES[target_timeframe]
    if bucket_minutes % source_timeframe_minutes != 0:
        raise ValueError(
            f"target {target_timeframe} ({bucket_minutes}min) must be a multiple "
            f"of source ({source_timeframe_minutes}min)"
        )
    expected_subbars = bucket_minutes // source_timeframe_minutes

    buckets: dict[datetime, list[OhlcvBar]] = {}
    for bar in bars:
        key = _bucket_start(bar.open_time, bucket_minutes)
        buckets.setdefault(key, []).append(bar)

    # Determine which buckets count as "closed" — a bucket is closed once we
    # have observed a source bar whose open_time falls in a LATER bucket.
    # SOL/JPY 15m has recurring maintenance gaps (~weekly, ~2-3h) so middle
    # buckets often have fewer than expected_subbars sub-bars but still
    # represent a real, fully-elapsed period. The only bucket we must drop is
    # the very last one if it is still in progress (no later bar observed).
    sorted_keys = sorted(buckets)
    if not sorted_keys:
        return []
    last_observed_bucket = sorted_keys[-1]
    last_bar_in_last_bucket = max(b.open_time for b in buckets[last_observed_bucket])
    last_bucket_complete = (
        last_bar_in_last_bucket
        >= last_observed_bucket + timedelta(minutes=bucket_minutes - source_timeframe_minutes)
    )

    aggregated: list[OhlcvBar] = []
    for key in sorted_keys:
        subbars = buckets[key]
        if key == last_observed_bucket and not last_bucket_complete:
            continue
        aggregated.append(
            OhlcvBar(
                open_time=key,
                close_time=key + timedelta(minutes=bucket_minutes),
                open=subbars[0].open,
                high=max(b.high for b in subbars),
                low=min(b.low for b in subbars),
                close=subbars[-1].close,
                volume=sum(b.volume for b in subbars),
            )
        )
    return aggregated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="source 15m OHLCV CSV path")
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=sorted(TIMEFRAME_TO_MINUTES),
        help="target timeframe",
    )
    parser.add_argument("--output", required=True, help="output CSV path")
    parser.add_argument(
        "--source-minutes",
        type=int,
        default=15,
        help="source bar period in minutes (default 15)",
    )
    args = parser.parse_args()

    source_bars = read_bars_from_csv(args.input)
    aggregated = resample(
        bars=source_bars,
        target_timeframe=args.timeframe,
        source_timeframe_minutes=args.source_minutes,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_bars_to_csv(output, aggregated)
    first = aggregated[0].open_time.isoformat().replace("+00:00", "Z") if aggregated else None
    last = aggregated[-1].open_time.isoformat().replace("+00:00", "Z") if aggregated else None
    print(
        "[resample] done",
        {
            "input": args.input,
            "input_bars": len(source_bars),
            "output": str(output),
            "output_bars": len(aggregated),
            "timeframe": args.timeframe,
            "first_open": first,
            "last_open": last,
        },
    )


if __name__ == "__main__":
    main()
