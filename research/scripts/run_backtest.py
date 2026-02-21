from __future__ import annotations

import argparse
from pathlib import Path

from research.src.app.backtest_usecase import BacktestInput, run_backtest_usecase
from research.src.infra.research_config import load_bot_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline backtest with shared pybot strategy")
    parser.add_argument("--config", required=True, help="JSON config file path")
    parser.add_argument("--bars", required=True, help="OHLCV CSV file path")
    parser.add_argument(
        "--output",
        default="research/data/processed/backtest_latest.json",
        help="output report JSON path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_bot_config(args.config)

    report = run_backtest_usecase(
        BacktestInput(
            config=config,
            bars_path=args.bars,
            output_path=args.output,
        )
    )

    top_reasons = sorted(
        report.no_signal_reason_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    print("[research] backtest completed", report.summary.to_dict())
    print("[research] top no-signal reasons", top_reasons)
    print("[research] report", str(Path(args.output)))


if __name__ == "__main__":
    main()
