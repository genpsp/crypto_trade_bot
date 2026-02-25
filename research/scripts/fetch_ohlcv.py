from __future__ import annotations

import argparse
from pathlib import Path

from pybot.adapters.market_data.ohlcv_provider import OhlcvProvider
from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_bars_to_csv

TIMEFRAME_TO_BARS_PER_DAY = {"15m": 96, "2h": 12, "4h": 6}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OHLCV data for research")
    parser.add_argument("--pair", default="SOL/USDC", choices=["SOL/USDC"])
    parser.add_argument("--timeframe", default="2h", choices=["15m", "2h", "4h"])
    parser.add_argument(
        "--years",
        type=float,
        default=2.0,
        help="historical years to fetch when --limit is omitted (default: 2.0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="explicit bar count. if set, this overrides --years",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="force re-fetch even when output CSV already exists",
    )
    parser.add_argument(
        "--output",
        default="research/data/raw/solusdc_2h.csv",
        help="output CSV path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pair = args.pair
    timeframe = args.timeframe
    bars_per_day = TIMEFRAME_TO_BARS_PER_DAY[timeframe]
    target_bars = args.limit if args.limit is not None else int(args.years * 365 * bars_per_day)
    if target_bars <= 0:
        raise ValueError(f"target bars must be positive, got {target_bars}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists() and not args.refresh:
        cached_bars = read_bars_from_csv(output)
        if len(cached_bars) >= target_bars:
            print(
                "[research] ohlcv reused",
                {
                    "pair": pair,
                    "timeframe": timeframe,
                    "target_bars": target_bars,
                    "bars": len(cached_bars),
                    "first_close": cached_bars[0].close_time.isoformat().replace("+00:00", "Z"),
                    "last_close": cached_bars[-1].close_time.isoformat().replace("+00:00", "Z"),
                    "output": str(output),
                },
            )
            return

    provider = OhlcvProvider()
    if target_bars <= 1000:
        bars = provider.fetch_bars(pair=pair, timeframe=timeframe, limit=target_bars)
    else:
        bars = provider.fetch_bars_backfill(pair=pair, timeframe=timeframe, total_limit=target_bars)
    write_bars_to_csv(output, bars)

    print(
        "[research] ohlcv fetched",
        {
            "pair": pair,
            "timeframe": timeframe,
            "target_bars": target_bars,
            "bars": len(bars),
            "first_close": bars[0].close_time.isoformat().replace("+00:00", "Z"),
            "last_close": bars[-1].close_time.isoformat().replace("+00:00", "Z"),
            "output": str(output),
        },
    )


if __name__ == "__main__":
    main()
