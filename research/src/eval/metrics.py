from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
import math
from statistics import mean, pstdev
from typing import Any

from research.src.domain.backtest_types import BacktestReport, BacktestTrade
from research.src.eval.statistics import bootstrap_ci, deflated_sharpe

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_BLOCK_SIZE = 10


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _closed_trades(report: BacktestReport) -> list[BacktestTrade]:
    return [trade for trade in report.trades if trade.exit_reason != "OPEN"]


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return max_drawdown


def _daily_scaled_pnls(trades: list[BacktestTrade]) -> list[float]:
    by_day: dict[str, float] = defaultdict(float)
    for trade in trades:
        if trade.scaled_pnl_pct is None:
            continue
        resolved_time = _parse_time(trade.exit_time) or _parse_time(trade.entry_time)
        if resolved_time is None:
            continue
        by_day[resolved_time.date().isoformat()] += float(trade.scaled_pnl_pct)
    return [by_day[key] for key in sorted(by_day.keys())]


def _total_scaled(trades: list[BacktestTrade]) -> float:
    return sum(float(trade.scaled_pnl_pct) for trade in trades if trade.scaled_pnl_pct is not None)


def _win_rate(trades: list[BacktestTrade]) -> float:
    if not trades:
        return 0.0
    return sum(1 for trade in trades if trade.exit_reason == "TAKE_PROFIT") / len(trades) * 100


def _return_to_dd(trades: list[BacktestTrade]) -> float | None:
    values = [float(trade.scaled_pnl_pct) for trade in trades if trade.scaled_pnl_pct is not None]
    drawdown = _max_drawdown(values)
    if drawdown == 0:
        return None
    return sum(values) / abs(drawdown)


def _average_r(trades: list[BacktestTrade]) -> float:
    values = [float(trade.r_multiple) for trade in trades if trade.r_multiple is not None]
    return mean(values) if values else 0.0


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _add_ci(summary: dict[str, Any], trades: list[BacktestTrade]) -> None:
    metrics = {
        "total_scaled_pnl_pct": _total_scaled,
        "win_rate_pct": _win_rate,
        "return_to_dd": _return_to_dd,
        "average_r_multiple": _average_r,
    }
    for name, func in metrics.items():
        low, high = bootstrap_ci(trades, func, n_resamples=BOOTSTRAP_RESAMPLES, block_size=BOOTSTRAP_BLOCK_SIZE)
        summary[f"{name}_ci_low"] = round(low, 6)
        summary[f"{name}_ci_high"] = round(high, 6)


def _summary_for_trade_group(trades: list[BacktestTrade]) -> dict[str, Any]:
    scaled_values = [float(trade.scaled_pnl_pct) for trade in trades if trade.scaled_pnl_pct is not None]
    wins = sum(1 for trade in trades if trade.exit_reason == "TAKE_PROFIT")
    r_values = [float(trade.r_multiple) for trade in trades if trade.r_multiple is not None]
    return {
        "trades": len(trades),
        "total_pnl_pct": round(sum(float(trade.pnl_pct or 0.0) for trade in trades), 6),
        "total_scaled_pnl_pct": round(sum(scaled_values), 6),
        "win_rate_pct": round((wins / len(trades) * 100) if trades else 0.0, 6),
        "average_r_multiple": round(mean(r_values), 6) if r_values else 0.0,
    }


def _by_regime(trades: list[BacktestTrade]) -> dict[str, dict[str, dict[str, Any]]]:
    buckets: dict[str, dict[str, list[BacktestTrade]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        regime = trade.entry_regime or {}
        for dimension, label in regime.items():
            buckets[str(dimension)][str(label)].append(trade)
    return {
        dimension: {label: _summary_for_trade_group(group) for label, group in sorted(labels.items())}
        for dimension, labels in sorted(buckets.items())
    }


def compute_summary(
    report: BacktestReport,
    *,
    n_trials: int = 1,
    min_trades: int | None = None,
) -> dict[str, Any]:
    base = report.summary.to_dict()
    closed_trades = _closed_trades(report)
    scaled_values = [float(trade.scaled_pnl_pct) for trade in closed_trades if trade.scaled_pnl_pct is not None]
    r_values = [float(trade.r_multiple) for trade in closed_trades if trade.r_multiple is not None]
    half_index = len(scaled_values) // 2
    max_drawdown = _max_drawdown(scaled_values)
    total_scaled_pnl_pct = round(sum(scaled_values), 6)
    positive_sum = sum(value for value in scaled_values if value > 0)
    negative_sum = sum(value for value in scaled_values if value < 0)
    daily_values = _daily_scaled_pnls(closed_trades)
    sharpe_proxy = 0.0
    if len(daily_values) >= 2:
        daily_std = pstdev(daily_values)
        if daily_std > 0:
            sharpe_proxy = (mean(daily_values) / daily_std) * (365**0.5)
    multiplier_counts = Counter(float(trade.position_size_multiplier or 0.0) for trade in closed_trades)
    dsr, dsr_p_value = deflated_sharpe(scaled_values, n_trials=max(1, n_trials))

    base.update(
        {
            "closed_trades": len(closed_trades),
            "total_scaled_pnl_pct": total_scaled_pnl_pct,
            "second_half_scaled_pnl_pct": round(sum(scaled_values[half_index:]), 6),
            "max_drawdown_pct_points": round(max_drawdown, 6),
            "return_to_dd": round(total_scaled_pnl_pct / abs(max_drawdown), 6) if max_drawdown else None,
            "average_r_multiple": round(mean(r_values), 6) if r_values else 0.0,
            "profit_factor": round(positive_sum / abs(negative_sum), 6) if negative_sum else None,
            "expectancy_pct": round(mean(scaled_values), 6) if scaled_values else 0.0,
            "sharpe_proxy": round(sharpe_proxy, 6),
            "deflated_sharpe": round(dsr, 6),
            "deflated_sharpe_p_value": round(dsr_p_value, 6),
            "dsr_p_value": round(dsr_p_value, 6),
            "position_size_multiplier_counts": dict(sorted(multiplier_counts.items())),
            "by_regime": _by_regime(closed_trades),
        }
    )
    if min_trades is not None:
        base["min_trades"] = int(min_trades)
        base["rank_eligible"] = len(closed_trades) >= int(min_trades)
        base["rank_exclusion_reason"] = None if base["rank_eligible"] else f"CLOSED_TRADES_LT_{int(min_trades)}"
    _add_ci(base, closed_trades)
    return base
