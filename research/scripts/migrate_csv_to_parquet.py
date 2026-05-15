from __future__ import annotations

import argparse
from datetime import UTC, datetime

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.partitioned_cache import PartitionedOhlcvCache
from research.src.data.source_registry import infer_broker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy OHLCV CSV into monthly parquet cache")
    parser.add_argument("--input", required=True, help="legacy OHLCV CSV path")
    parser.add_argument("--broker", choices=["DEX", "GMO_COIN"], default=None)
    parser.add_argument("--pair", required=True, choices=["SOL/USDC", "SOL/JPY"])
    parser.add_argument("--timeframe", required=True, choices=["15m", "2h", "4h"])
    parser.add_argument("--cache-root", default="research/data/cache")
    parser.add_argument("--source", default="legacy_csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    broker = args.broker or infer_broker(args.pair)
    bars = read_bars_from_csv(args.input)
    cache = PartitionedOhlcvCache(args.cache_root)
    manifest = cache.append_bars(
        broker=broker,
        pair=args.pair,
        timeframe=args.timeframe,
        bars=bars,
        source=args.source,
        fetched_at=datetime.now(tz=UTC),
    )
    print(
        "[research] csv migrated to parquet cache",
        {
            "input": args.input,
            "broker": broker,
            "pair": args.pair,
            "timeframe": args.timeframe,
            "bars": len(bars),
            "cached_bars": manifest["bar_count"],
            "manifest": str(cache.manifest_path(broker=broker, pair=args.pair, timeframe=args.timeframe)),
            "gaps": len(manifest["gaps"]),
        },
    )


if __name__ == "__main__":
    main()
