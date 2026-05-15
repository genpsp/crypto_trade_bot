from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any

from research.src.domain.backtest_types import BacktestReport, BacktestTrade


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


def compute_summary(report: BacktestReport) -> dict[str, Any]:
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
            "position_size_multiplier_counts": dict(sorted(multiplier_counts.items())),
        }
    )
    return base
