from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.dex_bot.domain.risk.loss_streak_trade_cap import LOSS_STREAK_LOOKBACK_CLOSED_TRADES
from apps.dex_bot.domain.risk.loss_streak_trade_cap import resolve_effective_max_trades_per_day_for_strategy
from apps.dex_bot.domain.risk.short_regime_guard import resolve_short_regime_guard_state
from apps.dex_bot.domain.risk.short_stop_loss_cooldown import (
    SHORT_STOP_LOSS_COOLDOWN_BARS,
    is_short_stop_loss_cooldown_enabled,
)
from apps.dex_bot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_max_loss_stop_price_for_short,
    calculate_take_profit_price,
    calculate_take_profit_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from apps.dex_bot.domain.utils.time import get_bar_duration_seconds
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
    UPPER_TREND_EMA_FAST_PERIOD,
    UPPER_TREND_EMA_SLOW_PERIOD,
    UPPER_TREND_TIMEFRAME_MINUTES,
    _build_upper_timeframe_closes,
    _calculate_ema_gap_pct,
)
from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_json
from research.src.domain.backtest_engine import (
    _evaluate_strategy_for_backtest,
    _resolve_effective_notional,
    _resolve_entry_direction,
    _resolve_initial_quote_balance,
    _resolve_ohlcv_limit,
    _resolve_position_size_multiplier,
    _simulate_buy_fill_price,
    _simulate_sell_fill_price,
)
from research.src.infra.research_config import load_bot_config
from shared.indicators.ta import ema_series
from shared.utils.math import round_to


DEFAULT_CONFIG = "research/models/gmo_ema_pullback_15m_both_v0/config/current.json"
DEFAULT_BARS = "research/data/raw/soljpy_15m_1y.csv"
DEFAULT_OUTPUT = "research/data/processed/gmo_15m_loss_regime_latest.json"
DEFAULT_LATE_CUTOFF = "2025-10-01T00:00:00+00:00"


@dataclass
class ReplayedTrade:
    entry_index: int
    entry_time: str
    direction: str
    diagnostics: dict[str, Any]
    exit_reason: str
    scaled_pnl_pct: float
    pnl_pct: float
    holding_bars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_index": self.entry_index,
            "entry_time": self.entry_time,
            "direction": self.direction,
            "diagnostics": self.diagnostics,
            "exit_reason": self.exit_reason,
            "scaled_pnl_pct": self.scaled_pnl_pct,
            "pnl_pct": self.pnl_pct,
            "holding_bars": self.holding_bars,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze losing-trade regime characteristics for GMO 15m model")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="JSON config file path")
    parser.add_argument("--bars", default=DEFAULT_BARS, help="OHLCV CSV file path")
    parser.add_argument("--late-cutoff", default=DEFAULT_LATE_CUTOFF, help="ISO timestamp separating late regime")
    parser.add_argument("--output", default=None, help="optional output report JSON path")
    return parser.parse_args()


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    def pick(ratio: float) -> float:
        index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio)))
        return ordered[index]
    return {
        "q25": pick(0.25),
        "q50": pick(0.50),
        "q75": pick(0.75),
    }


def _calculate_upper_trend_metrics(decision_bars: list[Any]) -> dict[str, float | int | None]:
    upper_closes = _build_upper_timeframe_closes(decision_bars, UPPER_TREND_TIMEFRAME_MINUTES)
    upper_fast_series = ema_series(upper_closes, UPPER_TREND_EMA_FAST_PERIOD)
    upper_slow_series = ema_series(upper_closes, UPPER_TREND_EMA_SLOW_PERIOD)
    upper_fast = upper_fast_series[-1] if upper_fast_series else None
    upper_slow = upper_slow_series[-1] if upper_slow_series else None

    upper_fast_slope_pct = None
    if len(upper_fast_series) >= 2 and upper_fast_series[-2] not in (None, 0):
        prev = upper_fast_series[-2]
        curr = upper_fast_series[-1]
        if prev is not None and curr is not None and prev != 0:
            upper_fast_slope_pct = ((curr - prev) / abs(prev)) * 100

    upper_close_drift_pct = None
    if len(upper_closes) >= 3 and upper_closes[-3] != 0:
        upper_close_drift_pct = ((upper_closes[-1] - upper_closes[-3]) / abs(upper_closes[-3])) * 100

    return {
        "upper_trend_gap_pct_computed": _calculate_ema_gap_pct(upper_fast, upper_slow),
        "upper_fast_slope_pct": upper_fast_slope_pct,
        "upper_close_drift_pct_3": upper_close_drift_pct,
        "upper_closes_count": len(upper_closes),
    }


def _calculate_lower_ema_gap_pct(diagnostics: dict[str, Any]) -> float | None:
    ema_fast = diagnostics.get("ema_fast")
    ema_slow = diagnostics.get("ema_slow")
    if not isinstance(ema_fast, (int, float)) or not isinstance(ema_slow, (int, float)):
        return None
    return _calculate_ema_gap_pct(float(ema_fast), float(ema_slow))


def _replay_trades(config: dict[str, Any], bars: list[Any]) -> list[ReplayedTrade]:
    direction = config["direction"]
    configured_min_notional_usdc = float(config["execution"]["min_notional_usdc"])
    slippage_bps = int(config["execution"]["slippage_bps"])
    max_trades_per_day = int(config["risk"]["max_trades_per_day"])
    max_loss_per_trade_pct = float(config["risk"]["max_loss_per_trade_pct"])
    take_profit_r_multiple = float(config["exit"]["take_profit_r_multiple"])
    ohlcv_limit = _resolve_ohlcv_limit(config)
    bar_duration_seconds = get_bar_duration_seconds(config["signal_timeframe"])
    portfolio_quote_usdc = _resolve_initial_quote_balance(config)

    open_position: dict[str, Any] | None = None
    recent_closed_trades: list[dict[str, Any]] = []
    closed_exit_reasons: list[str] = []
    latest_short_close_reason: str | None = None
    latest_short_close_index: int | None = None
    daily_entry_counts: dict[str, int] = {}
    completed: list[ReplayedTrade] = []

    for index, current_bar in enumerate(bars):
        if open_position is not None:
            if index <= open_position["entry_index"]:
                continue

            is_long = open_position["direction"] == "LONG"
            if is_long:
                stop_hit = current_bar.low <= open_position["stop_price"]
                tp_hit = current_bar.high >= open_position["take_profit_price"]
            else:
                stop_hit = current_bar.high >= open_position["stop_price"]
                tp_hit = current_bar.low <= open_position["take_profit_price"]

            if stop_hit or tp_hit:
                if stop_hit and tp_hit:
                    exit_reason = "STOP_LOSS_AND_TP_SAME_BAR"
                    exit_trigger_price = open_position["stop_price"]
                elif stop_hit:
                    exit_reason = "STOP_LOSS"
                    exit_trigger_price = open_position["stop_price"]
                else:
                    exit_reason = "TAKE_PROFIT"
                    exit_trigger_price = open_position["take_profit_price"]

                if is_long:
                    exit_price = _simulate_sell_fill_price(exit_trigger_price, slippage_bps)
                    risk_per_unit = open_position["entry_price"] - open_position["stop_price"]
                    pnl_per_unit = exit_price - open_position["entry_price"]
                else:
                    exit_price = _simulate_buy_fill_price(exit_trigger_price, slippage_bps)
                    risk_per_unit = open_position["stop_price"] - open_position["entry_price"]
                    pnl_per_unit = open_position["entry_price"] - exit_price

                position_pnl_usdc = open_position["quantity_sol"] * pnl_per_unit
                portfolio_after_exit = open_position["base_notional_usdc"] + position_pnl_usdc
                pnl_pct = (pnl_per_unit / open_position["entry_price"]) * 100
                scaled_pnl_pct = (
                    ((portfolio_after_exit / open_position["base_notional_usdc"]) - 1) * 100
                    if open_position["base_notional_usdc"] > 0
                    else 0.0
                )
                r_multiple = (pnl_per_unit / risk_per_unit) if risk_per_unit > 0 else 0.0

                open_position["diagnostics"]["resolved_r_multiple"] = round_to(r_multiple, 6)
                completed.append(
                    ReplayedTrade(
                        entry_index=open_position["entry_index"],
                        entry_time=open_position["entry_time"],
                        direction=open_position["direction"],
                        diagnostics=open_position["diagnostics"],
                        exit_reason=exit_reason,
                        scaled_pnl_pct=round_to(scaled_pnl_pct, 6),
                        pnl_pct=round_to(pnl_pct, 6),
                        holding_bars=index - open_position["entry_index"],
                    )
                )

                normalized_close_reason = "STOP_LOSS" if exit_reason == "STOP_LOSS_AND_TP_SAME_BAR" else exit_reason
                close_time_iso = current_bar.close_time.isoformat().replace("+00:00", "Z")
                recent_closed_trades.insert(
                    0,
                    {
                        "direction": open_position["direction"],
                        "close_reason": normalized_close_reason,
                        "position": {"exit_time_iso": close_time_iso},
                        "updated_at": close_time_iso,
                    },
                )
                if len(recent_closed_trades) > LOSS_STREAK_LOOKBACK_CLOSED_TRADES:
                    recent_closed_trades = recent_closed_trades[:LOSS_STREAK_LOOKBACK_CLOSED_TRADES]
                if not is_long:
                    latest_short_close_reason = exit_reason
                    latest_short_close_index = index
                portfolio_quote_usdc = round_to(portfolio_after_exit, 10)
                closed_exit_reasons.append(exit_reason)
                open_position = None
            continue

        day_key = current_bar.close_time.date().isoformat()
        trades_today = daily_entry_counts.get(day_key, 0)
        recent_close_reasons = list(reversed(closed_exit_reasons[-LOSS_STREAK_LOOKBACK_CLOSED_TRADES:]))
        effective_max_trades_per_day, _, _ = resolve_effective_max_trades_per_day_for_strategy(
            strategy_name=config["strategy"]["name"],
            base_max_trades_per_day=max_trades_per_day,
            recent_close_reasons=recent_close_reasons,
        )
        if trades_today >= effective_max_trades_per_day:
            continue

        decision_window_start = max(0, index + 1 - ohlcv_limit)
        decision_bars = bars[decision_window_start : index + 1]
        decision = _evaluate_strategy_for_backtest(
            config=config,
            direction=direction,
            bars=decision_bars,
            strategy=config["strategy"],
            risk=config["risk"],
            exit=config["exit"],
            execution=config["execution"],
        )

        if decision.type == "NO_SIGNAL":
            continue

        entry_direction = _resolve_entry_direction(direction, decision.diagnostics)
        if (
            entry_direction == "SHORT"
            and is_short_stop_loss_cooldown_enabled(config["strategy"]["name"])
            and latest_short_close_reason == "STOP_LOSS"
            and latest_short_close_index is not None
        ):
            bars_since_short_stop_loss = index - latest_short_close_index
            if bars_since_short_stop_loss < SHORT_STOP_LOSS_COOLDOWN_BARS:
                continue

        if entry_direction == "SHORT":
            short_regime_guard_active, *_ = resolve_short_regime_guard_state(
                strategy_name=config["strategy"]["name"],
                recent_closed_trades=recent_closed_trades,
                current_bar_close_time=current_bar.close_time,
                bar_duration_seconds=bar_duration_seconds,
            )
            if short_regime_guard_active:
                continue

        daily_entry_counts[day_key] = trades_today + 1

        size_multiplier = _resolve_position_size_multiplier(decision.diagnostics)
        base_notional_usdc = round_to(portfolio_quote_usdc, 6)
        if base_notional_usdc < configured_min_notional_usdc:
            continue
        effective_notional_usdc = _resolve_effective_notional(base_notional_usdc, size_multiplier)
        if effective_notional_usdc <= 0:
            continue

        if entry_direction == "LONG":
            resolved_entry_price = _simulate_buy_fill_price(decision.entry_price, slippage_bps)
        else:
            resolved_entry_price = _simulate_sell_fill_price(decision.entry_price, slippage_bps)
        if resolved_entry_price <= 0:
            continue

        quantity_sol = effective_notional_usdc / resolved_entry_price
        if quantity_sol <= 0:
            continue

        swing_stop = float(decision.stop_price)
        if entry_direction == "LONG":
            pct_stop = calculate_max_loss_stop_price(resolved_entry_price, max_loss_per_trade_pct)
            final_stop = tighten_stop_for_long(resolved_entry_price, swing_stop, max_loss_per_trade_pct)
            if final_stop >= resolved_entry_price:
                final_stop = pct_stop
            if final_stop >= resolved_entry_price:
                continue
            take_profit_price = calculate_take_profit_price(
                resolved_entry_price,
                final_stop,
                take_profit_r_multiple,
            )
        else:
            pct_stop = calculate_max_loss_stop_price_for_short(
                resolved_entry_price,
                max_loss_per_trade_pct,
            )
            final_stop = tighten_stop_for_short(resolved_entry_price, swing_stop, max_loss_per_trade_pct)
            if final_stop <= resolved_entry_price:
                final_stop = pct_stop
            if final_stop <= resolved_entry_price:
                continue
            take_profit_price = calculate_take_profit_price_for_short(
                resolved_entry_price,
                final_stop,
                take_profit_r_multiple,
            )

        diagnostics = deepcopy(decision.diagnostics)
        diagnostics["lower_ema_gap_pct"] = _calculate_lower_ema_gap_pct(diagnostics)
        diagnostics.update(_calculate_upper_trend_metrics(decision_bars))
        diagnostics["resolved_entry_price"] = resolved_entry_price
        diagnostics["resolved_stop_price"] = final_stop
        diagnostics["resolved_take_profit_price"] = take_profit_price
        diagnostics["effective_notional_usdc"] = effective_notional_usdc
        diagnostics["entry_bar_close_time"] = current_bar.close_time.isoformat()

        open_position = {
            "entry_index": index,
            "entry_time": current_bar.close_time.isoformat(),
            "direction": entry_direction,
            "quantity_sol": quantity_sol,
            "entry_price": resolved_entry_price,
            "stop_price": final_stop,
            "take_profit_price": take_profit_price,
            "base_notional_usdc": base_notional_usdc,
            "diagnostics": diagnostics,
        }

    return completed


def _summarize_subset(name: str, trades: list[ReplayedTrade]) -> dict[str, Any]:
    features = [
        "upper_trend_gap_pct",
        "upper_fast_slope_pct",
        "upper_close_drift_pct_3",
        "lower_ema_gap_pct",
        "distance_from_ema_fast_pct",
        "rsi",
        "atr_pct",
        "stop_distance_pct",
    ]
    summary: dict[str, Any] = {
        "name": name,
        "count": len(trades),
        "direction_counts": dict(Counter(trade.direction for trade in trades)),
        "exit_reason_counts": dict(Counter(trade.exit_reason for trade in trades)),
        "avg_scaled_pnl_pct": round_to(_safe_mean([trade.scaled_pnl_pct for trade in trades]) or 0.0, 6),
    }
    feature_summary: dict[str, Any] = {}
    for feature in features:
        values = [
            float(trade.diagnostics[feature])
            for trade in trades
            if isinstance(trade.diagnostics.get(feature), (int, float))
        ]
        if not values:
            continue
        quantiles = _quantiles(values)
        feature_summary[feature] = {
            "mean": round_to(_safe_mean(values) or 0.0, 6),
            "min": round_to(min(values), 6),
            "max": round_to(max(values), 6),
            **({key: round_to(value, 6) for key, value in quantiles.items()} if quantiles else {}),
        }
    summary["feature_summary"] = feature_summary
    return summary


def _bucket_report(
    trades: list[ReplayedTrade],
    *,
    feature: str,
    bounds: list[float],
) -> list[dict[str, Any]]:
    ordered_bounds = [float("-inf"), *bounds, float("inf")]
    report: list[dict[str, Any]] = []
    for lower, upper in zip(ordered_bounds, ordered_bounds[1:]):
        bucket = [
            trade
            for trade in trades
            if isinstance(trade.diagnostics.get(feature), (int, float))
            and lower <= float(trade.diagnostics[feature]) < upper
        ]
        if not bucket:
            continue
        wins = sum(1 for trade in bucket if trade.exit_reason == "TAKE_PROFIT")
        total_scaled_pnl_pct = sum(trade.scaled_pnl_pct for trade in bucket)
        report.append(
            {
                "range": [lower, upper],
                "count": len(bucket),
                "win_rate_pct": round_to((wins / len(bucket)) * 100, 4),
                "total_scaled_pnl_pct": round_to(total_scaled_pnl_pct, 6),
                "average_scaled_pnl_pct": round_to(total_scaled_pnl_pct / len(bucket), 6),
                "direction_counts": dict(Counter(trade.direction for trade in bucket)),
            }
        )
    return report


def main() -> None:
    args = parse_args()
    config = load_bot_config(args.config)
    bars = read_bars_from_csv(args.bars)
    trades = _replay_trades(config, bars)

    late_trades = [trade for trade in trades if trade.entry_time >= args.late_cutoff]
    winners = [trade for trade in trades if trade.exit_reason == "TAKE_PROFIT"]
    losers = [trade for trade in trades if trade.exit_reason != "TAKE_PROFIT"]
    late_winners = [trade for trade in late_trades if trade.exit_reason == "TAKE_PROFIT"]
    late_losers = [trade for trade in late_trades if trade.exit_reason != "TAKE_PROFIT"]

    payload = {
        "config": str(Path(args.config)),
        "bars": str(Path(args.bars)),
        "late_cutoff": args.late_cutoff,
        "subsets": {
            "all": _summarize_subset("all", trades),
            "winners": _summarize_subset("winners", winners),
            "losers": _summarize_subset("losers", losers),
            "late_winners": _summarize_subset("late_winners", late_winners),
            "late_losers": _summarize_subset("late_losers", late_losers),
        },
        "bucket_reports": {
            "upper_trend_gap_pct": _bucket_report(trades, feature="upper_trend_gap_pct", bounds=[0.05, 0.075, 0.1, 0.125, 0.15, 0.2]),
            "upper_fast_slope_pct": _bucket_report(trades, feature="upper_fast_slope_pct", bounds=[-0.3, -0.15, 0.0, 0.05, 0.1, 0.2]),
            "upper_close_drift_pct_3": _bucket_report(trades, feature="upper_close_drift_pct_3", bounds=[-1.5, -0.5, 0.0, 0.5, 1.0, 1.5]),
            "lower_ema_gap_pct": _bucket_report(trades, feature="lower_ema_gap_pct", bounds=[0.05, 0.1, 0.15, 0.2, 0.3]),
            "atr_pct": _bucket_report(trades, feature="atr_pct", bounds=[0.5, 0.7, 0.9, 1.1]),
            "rsi": _bucket_report(trades, feature="rsi", bounds=[40, 45, 50, 55, 60, 65]),
        },
        "sample_late_losses": [trade.to_dict() for trade in late_losers[:10]],
    }

    print("[research] all", payload["subsets"]["all"]["count"])
    print("[research] late_losers", payload["subsets"]["late_losers"]["count"])
    print("[research] losers.upper_fast_slope_pct", payload["subsets"]["losers"]["feature_summary"].get("upper_fast_slope_pct"))
    print("[research] late_losers.upper_fast_slope_pct", payload["subsets"]["late_losers"]["feature_summary"].get("upper_fast_slope_pct"))
    print("[research] winners.upper_fast_slope_pct", payload["subsets"]["winners"]["feature_summary"].get("upper_fast_slope_pct"))
    print("[research] late_losers.upper_close_drift_pct_3", payload["subsets"]["late_losers"]["feature_summary"].get("upper_close_drift_pct_3"))
    print("[research] buckets.upper_fast_slope_pct")
    for row in payload["bucket_reports"]["upper_fast_slope_pct"]:
        print(" ", row)

    if args.output:
        write_json(args.output, payload)
        print("[research] report", args.output)


if __name__ == "__main__":
    main()
