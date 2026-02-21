from __future__ import annotations

import argparse
from pathlib import Path

from pybot.adapters.market_data.ohlcv_provider import OhlcvProvider
from research.src.adapters.csv_bar_repository import write_bars_to_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OHLCV data for research")
    parser.add_argument("--pair", default="SOL/USDC", choices=["SOL/USDC"])
    parser.add_argument("--timeframe", default="2h", choices=["2h", "4h"])
    parser.add_argument("--limit", type=int, default=1000)
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

    provider = OhlcvProvider()
    bars = provider.fetch_bars(pair=pair, timeframe=timeframe, limit=args.limit)
    output = Path(args.output)
    write_bars_to_csv(output, bars)

    print(
        "[research] ohlcv fetched",
        {
            "pair": pair,
            "timeframe": timeframe,
            "bars": len(bars),
            "first_close": bars[0].close_time.isoformat().replace("+00:00", "Z"),
            "last_close": bars[-1].close_time.isoformat().replace("+00:00", "Z"),
            "output": str(output),
        },
    )


if __name__ == "__main__":
    main()
