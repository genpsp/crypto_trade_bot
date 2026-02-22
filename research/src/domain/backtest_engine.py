from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pybot.domain.model.types import BotConfig, Direction, OhlcvBar
from pybot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_max_loss_stop_price_for_short,
    calculate_take_profit_price,
    calculate_take_profit_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from pybot.domain.strategy.registry import evaluate_strategy_for_model
from pybot.domain.utils.math import round_to

from research.src.domain.backtest_types import BacktestReport, BacktestSummary, BacktestTrade


@dataclass
class _OpenPosition:
    entry_index: int
    entry_time: datetime
    direction: Direction
    quantity_sol: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    position_size_multiplier: float
    base_notional_usdc: float
    effective_notional_usdc: float


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


BASE_PORTFOLIO_NOTIONAL_USDC = 100.0
SLIPPAGE_BPS_DENOMINATOR = 10_000
OHLCV_LIMIT = 300


def _resolve_position_size_multiplier(diagnostics: dict[str, Any] | None) -> float:
    if diagnostics is None:
        return 1.0
    raw = diagnostics.get("position_size_multiplier")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return 1.0


def _resolve_effective_notional(base_notional_usdc: float, size_multiplier: float) -> float:
    return round_to(base_notional_usdc * size_multiplier, 2)


def _slippage_ratio(slippage_bps: int) -> float:
    return max(0, slippage_bps) / SLIPPAGE_BPS_DENOMINATOR


def _simulate_buy_fill_price(trigger_price: float, slippage_bps: int) -> float:
    return trigger_price * (1 + _slippage_ratio(slippage_bps))


def _simulate_sell_fill_price(trigger_price: float, slippage_bps: int) -> float:
    return max(0.0, trigger_price * (1 - _slippage_ratio(slippage_bps)))


def run_backtest(bars: list[OhlcvBar], config: BotConfig) -> BacktestReport:
    if len(bars) < 2:
        raise ValueError("Backtest requires at least 2 OHLCV bars")
    direction = config["direction"]
    configured_min_notional_usdc = float(config["execution"]["min_notional_usdc"])
    slippage_bps = int(config["execution"]["slippage_bps"])
    max_trades_per_day = config["risk"]["max_trades_per_day"]
    max_loss_per_trade_pct = float(config["risk"]["max_loss_per_trade_pct"])
    take_profit_r_multiple = float(config["exit"]["take_profit_r_multiple"])
    portfolio_quote_usdc = BASE_PORTFOLIO_NOTIONAL_USDC

    open_position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    no_signal_reasons: Counter[str] = Counter()
    daily_entry_counts: dict[str, int] = {}
    enter_count = 0
    no_signal_count = 0

    for index, current_bar in enumerate(bars):
        if open_position is not None:
            if index <= open_position.entry_index:
                continue

            is_long = open_position.direction == "LONG_ONLY"
            if is_long:
                stop_hit = current_bar.low <= open_position.stop_price
                tp_hit = current_bar.high >= open_position.take_profit_price
            else:
                stop_hit = current_bar.high >= open_position.stop_price
                tp_hit = current_bar.low <= open_position.take_profit_price

            if stop_hit or tp_hit:
                if stop_hit and tp_hit:
                    exit_reason = "STOP_LOSS_AND_TP_SAME_BAR"
                    exit_trigger_price = open_position.stop_price
                elif stop_hit:
                    exit_reason = "STOP_LOSS"
                    exit_trigger_price = open_position.stop_price
                else:
                    exit_reason = "TAKE_PROFIT"
                    exit_trigger_price = open_position.take_profit_price

                if is_long:
                    exit_price = _simulate_sell_fill_price(exit_trigger_price, slippage_bps)
                    risk_per_unit = open_position.entry_price - open_position.stop_price
                    pnl_per_unit = exit_price - open_position.entry_price
                else:
                    exit_price = _simulate_buy_fill_price(exit_trigger_price, slippage_bps)
                    risk_per_unit = open_position.stop_price - open_position.entry_price
                    pnl_per_unit = open_position.entry_price - exit_price

                position_pnl_usdc = open_position.quantity_sol * pnl_per_unit
                portfolio_after_exit = open_position.base_notional_usdc + position_pnl_usdc
                pnl_pct = (pnl_per_unit / open_position.entry_price) * 100
                scaled_pnl_pct = (
                    ((portfolio_after_exit / open_position.base_notional_usdc) - 1) * 100
                    if open_position.base_notional_usdc > 0
                    else 0.0
                )
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
                        scaled_pnl_pct=round_to(scaled_pnl_pct, 6),
                        r_multiple=round_to(r_multiple, 6),
                        position_size_multiplier=round_to(open_position.position_size_multiplier, 4),
                        base_notional_usdc=round_to(open_position.base_notional_usdc, 2),
                        effective_notional_usdc=round_to(open_position.effective_notional_usdc, 2),
                        holding_bars=index - open_position.entry_index,
                    )
                )
                portfolio_quote_usdc = round_to(portfolio_after_exit, 10)
                open_position = None
            continue

        day_key = current_bar.close_time.astimezone(UTC).date().isoformat()
        trades_today = daily_entry_counts.get(day_key, 0)
        if trades_today >= max_trades_per_day:
            no_signal_count += 1
            no_signal_reasons["MAX_TRADES_PER_DAY_REACHED"] += 1
            continue

        decision_window_start = max(0, index + 1 - OHLCV_LIMIT)
        decision_bars = bars[decision_window_start : index + 1]
        decision = evaluate_strategy_for_model(
            direction=direction,
            bars=decision_bars,
            strategy=config["strategy"],
            risk=config["risk"],
            exit=config["exit"],
            execution=config["execution"],
        )

        if decision.type == "NO_SIGNAL":
            no_signal_count += 1
            no_signal_reasons[decision.reason] += 1
            continue

        # Live run_cycle counts each ENTER attempt toward max_trades_per_day even if execution later fails.
        daily_entry_counts[day_key] = trades_today + 1

        size_multiplier = _resolve_position_size_multiplier(decision.diagnostics)
        base_notional_usdc = round_to(portfolio_quote_usdc, 6)
        if base_notional_usdc < configured_min_notional_usdc:
            no_signal_count += 1
            no_signal_reasons["INSUFFICIENT_QUOTE_BALANCE_FOR_MIN_NOTIONAL"] += 1
            continue

        effective_notional_usdc = _resolve_effective_notional(base_notional_usdc, size_multiplier)
        if effective_notional_usdc <= 0:
            no_signal_count += 1
            no_signal_reasons["INVALID_EFFECTIVE_NOTIONAL"] += 1
            continue

        if direction == "LONG_ONLY":
            resolved_entry_price = _simulate_buy_fill_price(decision.entry_price, slippage_bps)
        else:
            resolved_entry_price = _simulate_sell_fill_price(decision.entry_price, slippage_bps)

        if resolved_entry_price <= 0:
            no_signal_count += 1
            no_signal_reasons["INVALID_ENTRY_FILL_PRICE"] += 1
            continue

        quantity_sol = effective_notional_usdc / resolved_entry_price
        if quantity_sol <= 0:
            no_signal_count += 1
            no_signal_reasons["INVALID_ENTRY_QUANTITY"] += 1
            continue

        swing_stop = float(decision.stop_price)
        if direction == "LONG_ONLY":
            pct_stop = calculate_max_loss_stop_price(resolved_entry_price, max_loss_per_trade_pct)
            final_stop = tighten_stop_for_long(
                resolved_entry_price,
                swing_stop,
                max_loss_per_trade_pct,
            )
            if final_stop >= resolved_entry_price:
                final_stop = pct_stop
            if final_stop >= resolved_entry_price:
                no_signal_count += 1
                no_signal_reasons["INVALID_RISK_AFTER_FILL"] += 1
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
            final_stop = tighten_stop_for_short(
                resolved_entry_price,
                swing_stop,
                max_loss_per_trade_pct,
            )
            if final_stop <= resolved_entry_price:
                final_stop = pct_stop
            if final_stop <= resolved_entry_price:
                no_signal_count += 1
                no_signal_reasons["INVALID_RISK_AFTER_FILL"] += 1
                continue
            take_profit_price = calculate_take_profit_price_for_short(
                resolved_entry_price,
                final_stop,
                take_profit_r_multiple,
            )

        enter_count += 1
        open_position = _OpenPosition(
            entry_index=index,
            entry_time=current_bar.close_time,
            direction=direction,
            quantity_sol=quantity_sol,
            entry_price=resolved_entry_price,
            stop_price=final_stop,
            take_profit_price=take_profit_price,
            position_size_multiplier=size_multiplier,
            base_notional_usdc=base_notional_usdc,
            effective_notional_usdc=effective_notional_usdc,
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
                scaled_pnl_pct=None,
                r_multiple=None,
                position_size_multiplier=round_to(open_position.position_size_multiplier, 4),
                base_notional_usdc=round_to(open_position.base_notional_usdc, 2),
                effective_notional_usdc=round_to(open_position.effective_notional_usdc, 2),
                holding_bars=None,
            )
        )

    closed_trades = [trade for trade in trades if trade.exit_reason != "OPEN"]
    wins = sum(1 for trade in closed_trades if trade.exit_reason == "TAKE_PROFIT")
    losses = len(closed_trades) - wins
    pnl_values = [trade.pnl_pct for trade in closed_trades if trade.pnl_pct is not None]
    scaled_pnl_values = [
        trade.scaled_pnl_pct for trade in closed_trades if trade.scaled_pnl_pct is not None
    ]
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
            average_scaled_pnl_pct=round_to(
                _safe_average([value for value in scaled_pnl_values if value is not None]), 6
            ),
            total_scaled_pnl_pct=round_to(sum(scaled_pnl_values), 6),
            average_r_multiple=round_to(_safe_average([value for value in r_values if value is not None]), 6),
            first_bar_close_time=_to_utc_iso(bars[0].close_time),
            last_bar_close_time=_to_utc_iso(bars[-1].close_time),
        ),
        no_signal_reason_counts=dict(no_signal_reasons),
        trades=trades,
    )
    return report
