from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import time
from typing import Any, Iterable, Iterator, Literal

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.market_dataset import MarketDataset
from research.src.domain.backtest_engine import run_backtest
from research.src.domain.backtest_types import BacktestReport
from research.src.eval.metrics import compute_summary
from research.src.eval.trial import TrialResult, TrialSpec

KeepTradesMode = Literal["none", "on-error", "all"]
KEEP_TRADES_MODES: set[str] = {"none", "on-error", "all"}


def _trade_dicts(report: BacktestReport) -> list[dict[str, Any]]:
    return [trade.to_dict() for trade in report.trades]


def _validate_keep_trades(keep_trades: str) -> None:
    if keep_trades not in KEEP_TRADES_MODES:
        raise ValueError(f"unsupported keep_trades mode: {keep_trades}")


def _run_trial_with_bars(
    trial: TrialSpec,
    bars: list[OhlcvBar],
    keep_trades: KeepTradesMode = "none",
    n_trials: int = 1,
) -> TrialResult:
    started = time.perf_counter()
    try:
        if len(bars) < 2:
            raise ValueError(f"trial window has too few bars: {len(bars)}")
        report = run_backtest(bars=bars, config=trial.config)
        min_trades_raw = trial.tags.get("min_trades")
        min_trades = int(min_trades_raw) if min_trades_raw is not None else None
        summary = compute_summary(report, n_trials=n_trials, min_trades=min_trades)
        summary["window_role"] = trial.window.role
        summary["window_id"] = trial.window.window_id
        summary["seed"] = trial.tags.get("seed")
        return TrialResult(
            trial_id=trial.trial_id,
            summary=summary,
            no_signal_reason_counts=report.no_signal_reason_counts,
            runtime_seconds=round(time.perf_counter() - started, 6),
            error=None,
            trades=_trade_dicts(report) if keep_trades == "all" else None,
        )
    except Exception as error:  # pragma: no cover - exercised via CLI safety path
        return TrialResult(
            trial_id=trial.trial_id,
            summary={},
            no_signal_reason_counts={},
            runtime_seconds=round(time.perf_counter() - started, 6),
            error=f"{type(error).__name__}: {error}",
            trades=[] if keep_trades == "on-error" else None,
        )


def run_trials(
    trials: Iterable[TrialSpec],
    datasets: dict[str, MarketDataset],
    *,
    workers: int = 1,
    keep_trades: KeepTradesMode = "none",
) -> Iterator[TrialResult]:
    _validate_keep_trades(keep_trades)
    materialized = list(trials)
    trial_inputs = []
    for trial in materialized:
        dataset = datasets[trial.dataset_key.stable_key()]
        trial_inputs.append((trial, trial.window.slice_bars(dataset.bars)))

    n_trials = len(materialized)
    if workers <= 1:
        for trial, bars in trial_inputs:
            yield _run_trial_with_bars(trial, bars, keep_trades, n_trials)
        return

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_trial_id = {
            executor.submit(_run_trial_with_bars, trial, bars, keep_trades, n_trials): trial.trial_id
            for trial, bars in trial_inputs
        }
        for future in as_completed(future_to_trial_id):
            yield future.result()
