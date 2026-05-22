from __future__ import annotations

import argparse
from pathlib import Path

from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_bars_to_csv
from research.src.data.partitioned_cache import PartitionedOhlcvCache, sync_ohlcv_cache
from research.src.data.source_registry import fetch_recent_bars, get_provider, infer_broker

TIMEFRAME_TO_BARS_PER_DAY = {"15m": 96, "2h": 12, "4h": 6}


def _build_provider(pair: str):
    return get_provider(broker=infer_broker(pair), pair=pair)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OHLCV data for research")
    parser.add_argument(
        "--pair",
        default="SOL/USDC",
        choices=["SOL/USDC", "SOL/JPY", "BTC/JPY", "ETH/JPY"],
    )
    parser.add_argument("--broker", default=None, choices=["DEX", "GMO_COIN"])
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
    parser.add_argument("--cache-root", default="research/data/cache")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="legacy mode: fetch directly to CSV without updating parquet cache",
    )
    return parser.parse_args()


def _fetch_direct(pair: str, timeframe: str, target_bars: int):
    provider = _build_provider(pair)
    return fetch_recent_bars(provider, pair=pair, timeframe=timeframe, limit=target_bars)


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

    broker = args.broker or infer_broker(pair)
    bars = None
    cache_updated = False
    if not args.no_cache:
        try:
            provider = get_provider(broker=broker, pair=pair)
            cache = PartitionedOhlcvCache(args.cache_root)
            sync_ohlcv_cache(
                cache=cache,
                provider=provider,
                broker=broker,
                pair=pair,
                timeframe=timeframe,
                limit=target_bars,
            )
            bars = cache.load_bars(broker=broker, pair=pair, timeframe=timeframe)[-target_bars:]
            cache_updated = True
        except RuntimeError as error:
            # Keep the legacy CSV workflow usable in environments where optional
            # parquet dependency is not installed yet.
            if "pyarrow" not in str(error).lower():
                raise
            print("[research] parquet cache skipped", str(error))

    if bars is None:
        bars = _fetch_direct(pair, timeframe, target_bars)
    write_bars_to_csv(output, bars)

    print(
        "[research] ohlcv fetched",
        {
            "broker": broker,
            "pair": pair,
            "timeframe": timeframe,
            "target_bars": target_bars,
            "bars": len(bars),
            "first_close": bars[0].close_time.isoformat().replace("+00:00", "Z"),
            "last_close": bars[-1].close_time.isoformat().replace("+00:00", "Z"),
            "output": str(output),
            "cache_updated": cache_updated,
        },
    )


if __name__ == "__main__":
    main()
