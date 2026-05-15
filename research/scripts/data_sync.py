from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from research.src.adapters.csv_bar_repository import write_bars_to_csv
from research.src.data.partitioned_cache import PartitionedOhlcvCache, sync_ohlcv_cache
from research.src.data.source_registry import get_provider, infer_broker


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync OHLCV data into monthly research parquet cache")
    parser.add_argument("--broker", choices=["DEX", "GMO_COIN"], default=None)
    parser.add_argument("--pair", required=True, choices=["SOL/USDC", "SOL/JPY"])
    parser.add_argument("--timeframe", required=True, choices=["15m", "2h", "4h"])
    parser.add_argument("--since", default=None, help="UTC ISO datetime/date used for first backfill")
    parser.add_argument("--limit", type=int, default=None, help="explicit fetch bar count override")
    parser.add_argument("--cache-root", default="research/data/cache")
    parser.add_argument("--output-csv", default=None, help="optional compatibility CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    broker = args.broker or infer_broker(args.pair)
    provider = get_provider(broker=broker, pair=args.pair)
    cache = PartitionedOhlcvCache(args.cache_root)
    result = sync_ohlcv_cache(
        cache=cache,
        provider=provider,
        broker=broker,
        pair=args.pair,
        timeframe=args.timeframe,
        since=_parse_datetime(args.since),
        limit=args.limit,
    )
    if args.output_csv is not None:
        bars = cache.load_bars(broker=broker, pair=args.pair, timeframe=args.timeframe)
        write_bars_to_csv(Path(args.output_csv), bars)
    print(
        "[research] ohlcv cache synced",
        {
            "broker": result.broker,
            "pair": result.pair,
            "timeframe": result.timeframe,
            "fetched_bars": result.fetched_bars,
            "cached_bars": result.cached_bars,
            "first_close": result.first_close_time,
            "last_close": result.last_close_time,
            "gaps": len(result.gaps),
            "manifest": str(result.manifest_path),
        },
    )


if __name__ == "__main__":
    main()
