from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from pybot.domain.model.types import BotConfig, OhlcvBar
from pybot.domain.strategy.ema_trend_pullback_v0 import evaluate_ema_trend_pullback_v0
from pybot.domain.utils.math import round_to

from research.src.domain.backtest_types import BacktestReport, BacktestSummary, BacktestTrade


@dataclass
class _OpenPosition:
    entry_index: int
    entry_time: datetime
    entry_price: float
    stop_price: float
    take_profit_price: float


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def run_backtest(bars: list[OhlcvBar], config: BotConfig) -> BacktestReport:
    if len(bars) < 2:
        raise ValueError("Backtest requires at least 2 OHLCV bars")

    open_position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    no_signal_reasons: Counter[str] = Counter()
    enter_count = 0
    no_signal_count = 0

    for index, current_bar in enumerate(bars):
        if open_position is not None:
            if index <= open_position.entry_index:
                continue

            stop_hit = current_bar.low <= open_position.stop_price
            tp_hit = current_bar.high >= open_position.take_profit_price
            if stop_hit or tp_hit:
                if stop_hit and tp_hit:
                    # Same-bar TP/SL touch is ambiguous. Use conservative fill for reproducibility.
                    exit_reason = "STOP_LOSS_AND_TP_SAME_BAR"
                    exit_price = open_position.stop_price
                elif stop_hit:
                    exit_reason = "STOP_LOSS"
                    exit_price = open_position.stop_price
                else:
                    exit_reason = "TAKE_PROFIT"
                    exit_price = open_position.take_profit_price

                risk_per_unit = open_position.entry_price - open_position.stop_price
                pnl_per_unit = exit_price - open_position.entry_price
                pnl_pct = (pnl_per_unit / open_position.entry_price) * 100
                r_multiple = (pnl_per_unit / risk_per_unit) if risk_per_unit > 0 else 0.0

                trades.append(
                    BacktestTrade(
                        entry_time=_to_utc_iso(open_position.entry_time),
                        exit_time=_to_utc_iso(current_bar.close_time),
                        entry_price=round_to(open_position.entry_price, 6),
                        stop_price=round_to(open_position.stop_price, 6),
                        take_profit_price=round_to(open_position.take_profit_price, 6),
                        exit_price=round_to(exit_price, 6),
                        exit_reason=exit_reason,
                        pnl_pct=round_to(pnl_pct, 6),
                        r_multiple=round_to(r_multiple, 6),
                        holding_bars=index - open_position.entry_index,
                    )
                )
                open_position = None
            continue

        decision = evaluate_ema_trend_pullback_v0(
            bars=bars[: index + 1],
            strategy=config["strategy"],
            risk=config["risk"],
            exit=config["exit"],
            execution=config["execution"],
        )

        if decision.type == "NO_SIGNAL":
            no_signal_count += 1
            no_signal_reasons[decision.reason] += 1
            continue

        enter_count += 1
        open_position = _OpenPosition(
            entry_index=index,
            entry_time=current_bar.close_time,
            entry_price=decision.entry_price,
            stop_price=decision.stop_price,
            take_profit_price=decision.take_profit_price,
        )

    if open_position is not None:
        trades.append(
            BacktestTrade(
                entry_time=_to_utc_iso(open_position.entry_time),
                exit_time=None,
                entry_price=round_to(open_position.entry_price, 6),
                stop_price=round_to(open_position.stop_price, 6),
                take_profit_price=round_to(open_position.take_profit_price, 6),
                exit_price=None,
                exit_reason="OPEN",
                pnl_pct=None,
                r_multiple=None,
                holding_bars=None,
            )
        )

    closed_trades = [trade for trade in trades if trade.exit_reason != "OPEN"]
    wins = sum(1 for trade in closed_trades if trade.exit_reason == "TAKE_PROFIT")
    losses = len(closed_trades) - wins
    pnl_values = [trade.pnl_pct for trade in closed_trades if trade.pnl_pct is not None]
    r_values = [trade.r_multiple for trade in closed_trades if trade.r_multiple is not None]

    report = BacktestReport(
        summary=BacktestSummary(
            total_bars=len(bars),
            decision_enter_count=enter_count,
            decision_no_signal_count=no_signal_count,
            closed_trades=len(closed_trades),
            open_trades=len(trades) - len(closed_trades),
            wins=wins,
            losses=losses,
            win_rate_pct=round_to((wins / len(closed_trades) * 100) if closed_trades else 0.0, 4),
            average_pnl_pct=round_to(_safe_average([value for value in pnl_values if value is not None]), 6),
            total_pnl_pct=round_to(sum(pnl_values), 6),
            average_r_multiple=round_to(_safe_average([value for value in r_values if value is not None]), 6),
            first_bar_close_time=_to_utc_iso(bars[0].close_time),
            last_bar_close_time=_to_utc_iso(bars[-1].close_time),
        ),
        no_signal_reason_counts=dict(no_signal_reasons),
        trades=trades,
    )
    return report
