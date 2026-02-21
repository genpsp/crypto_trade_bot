from __future__ import annotations

from dataclasses import dataclass

from pybot.domain.model.types import BotConfig

from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_json
from research.src.domain.backtest_engine import run_backtest
from research.src.domain.backtest_types import BacktestReport


@dataclass
class BacktestInput:
    config: BotConfig
    bars_path: str
    output_path: str | None = None


def run_backtest_usecase(input_data: BacktestInput) -> BacktestReport:
    bars = read_bars_from_csv(input_data.bars_path)
    report = run_backtest(bars=bars, config=input_data.config)

    if input_data.output_path:
        write_json(input_data.output_path, report.to_dict())

    return report
