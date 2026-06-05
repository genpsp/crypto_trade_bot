from __future__ import annotations

from collections import Counter
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.dex_bot.domain.model.types import BotConfig, Direction, ModelDirection, OhlcvBar, TradeRecord
from apps.dex_bot.domain.risk.loss_streak_trade_cap import LOSS_STREAK_LOOKBACK_CLOSED_TRADES
from apps.dex_bot.domain.risk.loss_streak_trade_cap import resolve_effective_max_trades_per_day_for_strategy
from apps.dex_bot.domain.risk.short_regime_guard import (
    SHORT_REGIME_GUARD_REASON,
    resolve_short_regime_guard_state,
)
from apps.dex_bot.domain.risk.short_stop_loss_cooldown import (
    SHORT_STOP_LOSS_COOLDOWN_BARS,
    SHORT_STOP_LOSS_COOLDOWN_REASON,
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
from apps.dex_bot.domain.strategy.registry import evaluate_strategy_for_model as dex_evaluate_strategy_for_model
from apps.gmo_bot.domain.strategy.components import (
    BreakEvenAction,
    CloseAction,
    HoldAction,
    PartialTpAction,
    PositionContext,
    StrategyBundle,
    TrailAction,
    resolve_strategy_bundle,
)
from apps.gmo_bot.domain.strategy.registry import evaluate_strategy_for_model as gmo_evaluate_strategy_for_model
from shared.utils.math import round_to
from apps.dex_bot.domain.utils.time import get_bar_duration_seconds

from research.src.domain.backtest_types import BacktestReport, BacktestSummary, BacktestTrade
from research.src.data.regime_tagger import attach_regime_tags, get_bar_regime
from research.src.eval.execution_model import RejectedEntry, build_execution_model, buy_fill_price, sell_fill_price


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
    entry_regime: dict[str, str]
    execution_model_id: str
    execution_seed: int | None
    # ── component-bundle accounting (defaults preserve v0 numerics) ──
    initial_stop_price: float = 0.0
    initial_take_profit_price: float = 0.0
    atr_at_entry: float = 0.0
    initial_quantity_sol: float = 0.0
    initial_effective_notional_usdc: float = 0.0
    initial_base_notional_usdc: float = 0.0
    remaining_fraction: float = 1.0
    partial_pnl_accrued_usdc: float = 0.0


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


DEFAULT_INITIAL_QUOTE_BALANCE = 100.0
SLIPPAGE_BPS_DENOMINATOR = 10_000
DEFAULT_OHLCV_LIMIT = 300
OHLCV_LIMIT_FOR_15M_UPPER_TREND = 600

# Preserve the existing patch point for DEX-oriented tests and callers.
evaluate_strategy_for_model = dex_evaluate_strategy_for_model


def _resolve_entry_direction(
    config_direction: ModelDirection,
    diagnostics: dict[str, Any] | None,
) -> Direction:
    # LONG/SHORT fixed models must never be overridden by strategy diagnostics.
    if config_direction in ("LONG", "SHORT"):
        return config_direction

    raw = (diagnostics or {}).get("entry_direction")
    if raw in ("LONG", "SHORT"):
        return raw
    raise RuntimeError("BOTH config requires diagnostics.entry_direction to be LONG or SHORT on ENTER decision")


def _resolve_position_size_multiplier(diagnostics: dict[str, Any] | None) -> float:
    if diagnostics is None:
        return 1.0
    raw = diagnostics.get("position_size_multiplier")
    if isinstance(raw, (int, float)) and raw >= 0:
        return float(raw)
    return 1.0


def _resolve_effective_notional(base_notional_usdc: float, size_multiplier: float) -> float:
    return round_to(base_notional_usdc * size_multiplier, 2)


def _resolve_initial_quote_balance(config: BotConfig) -> float:
    raw = config["execution"].get("initial_quote_balance")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return float(raw)
    return DEFAULT_INITIAL_QUOTE_BALANCE


def _slippage_ratio(slippage_bps: int) -> float:
    return max(0, slippage_bps) / SLIPPAGE_BPS_DENOMINATOR


def _simulate_buy_fill_price(trigger_price: float, slippage_bps: int) -> float:
    # Backward-compatible wrapper for legacy analysis scripts. The engine itself
    # dispatches through ExecutionModel.
    return buy_fill_price(trigger_price, slippage_bps)


def _simulate_sell_fill_price(trigger_price: float, slippage_bps: int) -> float:
    # Backward-compatible wrapper for legacy analysis scripts. The engine itself
    # dispatches through ExecutionModel.
    return sell_fill_price(trigger_price, slippage_bps)


_STRATEGIES_REQUIRING_15M_UPPER_TREND_LIMIT = {
    "ema_trend_pullback_15m_v0",
    "ema_trend_pullback_15m_v2",
    "supertrend_15m_v0",
    "donchian_breakout_15m_v0",
    # router は trend regime で ema の上位足 EMA を評価するため同じ窓長が要る
    "regime_router_15m_v0",
}


def _resolve_ohlcv_limit(config: BotConfig) -> int:
    if config["strategy"]["name"] in _STRATEGIES_REQUIRING_15M_UPPER_TREND_LIMIT:
        return OHLCV_LIMIT_FOR_15M_UPPER_TREND
    return DEFAULT_OHLCV_LIMIT


_COMPONENT_BUNDLE_STRATEGIES = frozenset(
    {
        "ema_trend_pullback_15m_v2",
        "regime_router_15m_v0",
        "btc_leadlag_15m_v0",
        "donchian_breakout_15m_v0",
        "supertrend_15m_v0",
    }
)


def _strategy_uses_component_bundle(config: BotConfig) -> bool:
    return config["strategy"]["name"] in _COMPONENT_BUNDLE_STRATEGIES


def _evaluate_strategy_for_backtest(
    *,
    config: BotConfig,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: dict[str, Any],
    risk: dict[str, float | int],
    exit: dict[str, str | float],
    execution: dict[str, Any],
) -> Any:
    if config.get("broker") == "GMO_COIN":
        return gmo_evaluate_strategy_for_model(
            direction=direction,
            bars=bars,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )
    return evaluate_strategy_for_model(
        direction=direction,
        bars=bars,
        strategy=strategy,
        risk=risk,
        exit=exit,
        execution=execution,
    )


def run_backtest(bars: list[OhlcvBar], config: BotConfig) -> BacktestReport:
    if len(bars) < 2:
        raise ValueError("Backtest requires at least 2 OHLCV bars")
    # Ensure regime tags are present. When bars are shipped to worker processes via
    # ProcessPoolExecutor, dynamic attributes set by attach_regime_tags() do not survive
    # the default dataclass pickling, leaving entry_regime empty in trade parquet output.
    if not hasattr(bars[0], "regime"):
        attach_regime_tags(bars)
    direction = config["direction"]
    uses_components = _strategy_uses_component_bundle(config)
    strategy_bundle: StrategyBundle | None = (
        resolve_strategy_bundle(config["strategy"]) if uses_components else None
    )
    configured_min_notional_usdc = float(config["execution"]["min_notional_usdc"])
    slippage_bps = int(config["execution"]["slippage_bps"])
    max_trades_per_day = config["risk"]["max_trades_per_day"]
    max_loss_per_trade_pct = float(config["risk"]["max_loss_per_trade_pct"])
    take_profit_r_multiple = float(config["exit"]["take_profit_r_multiple"])
    ohlcv_limit = _resolve_ohlcv_limit(config)
    bar_duration_seconds = get_bar_duration_seconds(config["signal_timeframe"])
    portfolio_quote_usdc = _resolve_initial_quote_balance(config)
    execution_seed_raw = config["execution"].get("seed")
    execution_seed = int(execution_seed_raw) if isinstance(execution_seed_raw, int) or (isinstance(execution_seed_raw, str) and execution_seed_raw.isdigit()) else None
    rng = random.Random(execution_seed)
    execution_model = build_execution_model(config["execution"])

    open_position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    recent_closed_trades: list[TradeRecord] = []
    closed_exit_reasons: list[str] = []
    latest_short_close_reason: str | None = None
    latest_short_close_index: int | None = None
    no_signal_reasons: Counter[str] = Counter()
    daily_entry_counts: dict[str, int] = {}
    enter_count = 0
    no_signal_count = 0
    gate_state: dict[str, Any] = {"recent_r_multiples": []}

    def _record_close(
        *,
        position: _OpenPosition,
        current_bar: OhlcvBar,
        exit_price: float,
        exit_reason: str,
        bar_index: int,
    ) -> float:
        is_long_local = position.direction == "LONG"
        if is_long_local:
            risk_per_unit = position.entry_price - position.stop_price
            pnl_per_unit = exit_price - position.entry_price
        else:
            risk_per_unit = position.stop_price - position.entry_price
            pnl_per_unit = position.entry_price - exit_price
        position_pnl_usdc = position.quantity_sol * pnl_per_unit
        portfolio_after_exit = position.base_notional_usdc + position_pnl_usdc
        pnl_pct_local = (pnl_per_unit / position.entry_price) * 100
        scaled_pnl_pct_local = (
            ((portfolio_after_exit / position.base_notional_usdc) - 1) * 100
            if position.base_notional_usdc > 0
            else 0.0
        )
        r_multiple_local = (pnl_per_unit / risk_per_unit) if risk_per_unit > 0 else 0.0
        trades.append(
            BacktestTrade(
                entry_time=_to_utc_iso(position.entry_time),
                exit_time=_to_utc_iso(current_bar.close_time),
                entry_price=round_to(position.entry_price, 6),
                stop_price=round_to(position.stop_price, 6),
                take_profit_price=round_to(position.take_profit_price, 6),
                exit_price=round_to(exit_price, 6),
                exit_reason=exit_reason,
                pnl_pct=round_to(pnl_pct_local, 6),
                scaled_pnl_pct=round_to(scaled_pnl_pct_local, 6),
                r_multiple=round_to(r_multiple_local, 6),
                position_size_multiplier=round_to(position.position_size_multiplier, 4),
                base_notional_usdc=round_to(position.base_notional_usdc, 2),
                effective_notional_usdc=round_to(position.effective_notional_usdc, 2),
                holding_bars=bar_index - position.entry_index,
                entry_regime=dict(position.entry_regime),
                execution_model_id=position.execution_model_id,
                execution_seed=position.execution_seed,
            )
        )
        return portfolio_after_exit

    for index, current_bar in enumerate(bars):
        if open_position is not None:
            if index <= open_position.entry_index:
                continue

            if strategy_bundle is not None:
                context = PositionContext(
                    direction=open_position.direction,
                    entry_index=open_position.entry_index,
                    entry_price=open_position.entry_price,
                    stop_price=open_position.stop_price,
                    take_profit_price=open_position.take_profit_price,
                    atr_at_entry=open_position.atr_at_entry,
                    initial_stop_price=open_position.initial_stop_price,
                    initial_take_profit_price=open_position.initial_take_profit_price,
                )
                action = strategy_bundle.exit_policy.update(
                    position=context,
                    bar=current_bar,
                    bar_index=index,
                    config=config,
                )
                if isinstance(action, BreakEvenAction):
                    new_stop = open_position.entry_price * (1.0 + action.offset_pct / 100.0) \
                        if open_position.direction == "LONG" \
                        else open_position.entry_price * (1.0 - action.offset_pct / 100.0)
                    if (open_position.direction == "LONG" and new_stop > open_position.stop_price) or (
                        open_position.direction == "SHORT" and new_stop < open_position.stop_price
                    ):
                        open_position.stop_price = new_stop
                elif isinstance(action, TrailAction):
                    if (open_position.direction == "LONG" and action.new_stop_price > open_position.stop_price) or (
                        open_position.direction == "SHORT" and action.new_stop_price < open_position.stop_price
                    ):
                        open_position.stop_price = action.new_stop_price
                elif isinstance(action, CloseAction):
                    portfolio_after_exit = _record_close(
                        position=open_position,
                        current_bar=current_bar,
                        exit_price=float(action.price),
                        exit_reason=action.reason,
                        bar_index=index,
                    )
                    closed_exit_reasons.append(action.reason)
                    normalized = (
                        "STOP_LOSS" if action.reason == "STOP_LOSS_AND_TP_SAME_BAR" else action.reason
                    )
                    close_time_iso = _to_utc_iso(current_bar.close_time)
                    recent_closed_trades.insert(
                        0,
                        {
                            "direction": open_position.direction,
                            "close_reason": normalized,
                            "position": {"exit_time_iso": close_time_iso},
                            "updated_at": close_time_iso,
                        },
                    )
                    if len(recent_closed_trades) > LOSS_STREAK_LOOKBACK_CLOSED_TRADES:
                        recent_closed_trades = recent_closed_trades[:LOSS_STREAK_LOOKBACK_CLOSED_TRADES]
                    if open_position.direction == "SHORT":
                        latest_short_close_reason = action.reason
                        latest_short_close_index = index
                    portfolio_quote_usdc = round_to(
                        portfolio_after_exit + open_position.partial_pnl_accrued_usdc, 10
                    )
                    gate_state["recent_r_multiples"].append(trades[-1].r_multiple or 0.0)
                    open_position = None
                    continue
                elif isinstance(action, PartialTpAction):
                    fraction = max(0.0, min(1.0, float(action.fraction)))
                    if fraction <= 0 or fraction >= open_position.remaining_fraction:
                        # Treat as no-op if the requested partial is degenerate.
                        pass
                    else:
                        initial_qty = (
                            open_position.initial_quantity_sol
                            if open_position.initial_quantity_sol > 0
                            else open_position.quantity_sol
                        )
                        partial_qty = initial_qty * fraction
                        if partial_qty > open_position.quantity_sol:
                            partial_qty = open_position.quantity_sol
                        if open_position.direction == "LONG":
                            risk_per_unit = open_position.entry_price - open_position.stop_price
                            pnl_per_unit = float(action.price) - open_position.entry_price
                        else:
                            risk_per_unit = open_position.stop_price - open_position.entry_price
                            pnl_per_unit = open_position.entry_price - float(action.price)
                        partial_pnl_usdc = partial_qty * pnl_per_unit
                        initial_base = (
                            open_position.initial_base_notional_usdc
                            if open_position.initial_base_notional_usdc > 0
                            else open_position.base_notional_usdc
                        )
                        initial_eff = (
                            open_position.initial_effective_notional_usdc
                            if open_position.initial_effective_notional_usdc > 0
                            else open_position.effective_notional_usdc
                        )
                        partial_base = initial_base * fraction
                        partial_eff = initial_eff * fraction
                        pnl_pct_local = (pnl_per_unit / open_position.entry_price) * 100
                        # Use the original base_notional as the denominator so the
                        # row's scaled_pnl_pct is directly additive with the
                        # runner's eventual scaled_pnl_pct: both express change
                        # against the same deployed capital, and their sum equals
                        # the portfolio multiplier change for this entry.
                        scaled_pnl_pct_local = (
                            (partial_pnl_usdc / initial_base) * 100
                            if initial_base > 0
                            else 0.0
                        )
                        r_multiple_local = (
                            pnl_per_unit / risk_per_unit if risk_per_unit > 0 else 0.0
                        )
                        trades.append(
                            BacktestTrade(
                                entry_time=_to_utc_iso(open_position.entry_time),
                                exit_time=_to_utc_iso(current_bar.close_time),
                                entry_price=round_to(open_position.entry_price, 6),
                                stop_price=round_to(open_position.stop_price, 6),
                                take_profit_price=round_to(open_position.take_profit_price, 6),
                                exit_price=round_to(float(action.price), 6),
                                exit_reason=action.reason,
                                pnl_pct=round_to(pnl_pct_local, 6),
                                scaled_pnl_pct=round_to(scaled_pnl_pct_local, 6),
                                r_multiple=round_to(r_multiple_local, 6),
                                position_size_multiplier=round_to(
                                    open_position.position_size_multiplier, 4
                                ),
                                base_notional_usdc=round_to(partial_base, 2),
                                effective_notional_usdc=round_to(partial_eff, 2),
                                holding_bars=index - open_position.entry_index,
                                entry_regime=dict(open_position.entry_regime),
                                execution_model_id=open_position.execution_model_id,
                                execution_seed=open_position.execution_seed,
                            )
                        )
                        # Reduce only quantity_sol; keep base_notional / effective
                        # at their initial values so the runner's `_record_close`
                        # returns initial_base + runner_pnl. The partial portion's
                        # capital is reflected via partial_pnl_accrued, which is
                        # added to portfolio_quote when the runner exits.
                        open_position.quantity_sol -= partial_qty
                        open_position.remaining_fraction -= fraction
                        open_position.partial_pnl_accrued_usdc += partial_pnl_usdc
                # HoldAction or stop adjustment: fall through to standard touch check.

            is_long = open_position.direction == "LONG"
            if is_long:
                stop_hit = current_bar.low <= open_position.stop_price
                tp_hit = current_bar.high >= open_position.take_profit_price
            else:
                stop_hit = current_bar.high >= open_position.stop_price
                tp_hit = current_bar.low <= open_position.take_profit_price

            if stop_hit or tp_hit:
                if stop_hit and tp_hit:
                    exit_fill = execution_model.simulate_same_bar_stop_and_tp(
                        position=open_position,
                        bar=current_bar,
                        slippage_bps=slippage_bps,
                        rng=rng,
                    )
                elif stop_hit:
                    exit_fill = execution_model.simulate_stop_fill(
                        position=open_position,
                        bar=current_bar,
                        slippage_bps=slippage_bps,
                        rng=rng,
                    )
                else:
                    exit_fill = execution_model.simulate_tp_fill(
                        position=open_position,
                        bar=current_bar,
                        slippage_bps=slippage_bps,
                        rng=rng,
                    )
                exit_reason = exit_fill.reason
                exit_price = exit_fill.price
                portfolio_after_exit = _record_close(
                    position=open_position,
                    current_bar=current_bar,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    bar_index=index,
                )
                closed_exit_reasons.append(exit_reason)
                normalized_close_reason = (
                    "STOP_LOSS" if exit_reason == "STOP_LOSS_AND_TP_SAME_BAR" else exit_reason
                )
                close_time_iso = _to_utc_iso(current_bar.close_time)
                recent_closed_trades.insert(
                    0,
                    {
                        "direction": open_position.direction,
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
                portfolio_quote_usdc = round_to(
                    portfolio_after_exit + open_position.partial_pnl_accrued_usdc, 10
                )
                gate_state["recent_r_multiples"].append(trades[-1].r_multiple or 0.0)
                open_position = None
            continue

        day_key = current_bar.close_time.astimezone(UTC).date().isoformat()
        trades_today = daily_entry_counts.get(day_key, 0)
        recent_close_reasons = list(reversed(closed_exit_reasons[-LOSS_STREAK_LOOKBACK_CLOSED_TRADES:]))
        effective_max_trades_per_day, _, _ = resolve_effective_max_trades_per_day_for_strategy(
            strategy_name=config["strategy"]["name"],
            base_max_trades_per_day=max_trades_per_day,
            recent_close_reasons=recent_close_reasons,
        )
        if trades_today >= effective_max_trades_per_day:
            no_signal_count += 1
            no_signal_reasons["MAX_TRADES_PER_DAY_REACHED"] += 1
            continue

        if strategy_bundle is not None and not strategy_bundle.regime_gate.allow(
            bars=bars,
            index=index,
            config=config,
            gate_state=gate_state,
        ):
            no_signal_count += 1
            no_signal_reasons[strategy_bundle.regime_gate.reject_reason()] += 1
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
            no_signal_count += 1
            no_signal_reasons[decision.reason] += 1
            continue

        entry_direction = _resolve_entry_direction(direction, decision.diagnostics)
        if strategy_bundle is not None and not strategy_bundle.regime_gate.allow_for_direction(
            direction=entry_direction,
            bars=bars,
            index=index,
            config=config,
            gate_state=gate_state,
        ):
            no_signal_count += 1
            no_signal_reasons[
                strategy_bundle.regime_gate.reject_reason() + "_DIR"
            ] += 1
            continue
        if (
            entry_direction == "SHORT"
            and is_short_stop_loss_cooldown_enabled(config["strategy"]["name"])
            and latest_short_close_reason == "STOP_LOSS"
            and latest_short_close_index is not None
        ):
            bars_since_short_stop_loss = index - latest_short_close_index
            if bars_since_short_stop_loss < SHORT_STOP_LOSS_COOLDOWN_BARS:
                no_signal_count += 1
                no_signal_reasons[SHORT_STOP_LOSS_COOLDOWN_REASON] += 1
                continue

        if entry_direction == "SHORT":
            (
                short_regime_guard_active,
                _short_regime_guard_consecutive_stop_losses,
                _short_regime_guard_remaining_bars,
                _short_regime_guard_recent_short_trades,
                _short_regime_guard_recent_short_win_rate_pct,
            ) = resolve_short_regime_guard_state(
                strategy_name=config["strategy"]["name"],
                recent_closed_trades=recent_closed_trades,
                current_bar_close_time=current_bar.close_time,
                bar_duration_seconds=bar_duration_seconds,
            )
            if short_regime_guard_active:
                no_signal_count += 1
                no_signal_reasons[SHORT_REGIME_GUARD_REASON] += 1
                continue

        # Live run_cycle counts each ENTER attempt toward max_trades_per_day even if execution later fails.
        daily_entry_counts[day_key] = trades_today + 1

        if strategy_bundle is not None:
            size_multiplier = strategy_bundle.sizing_policy.size_multiplier(
                decision=decision, config=config
            )
        else:
            size_multiplier = _resolve_position_size_multiplier(decision.diagnostics)
        base_notional_usdc = round_to(portfolio_quote_usdc, 6)
        if base_notional_usdc < configured_min_notional_usdc:
            no_signal_count += 1
            no_signal_reasons["INSUFFICIENT_QUOTE_BALANCE_FOR_MIN_NOTIONAL"] += 1
            continue

        effective_notional_usdc = _resolve_effective_notional(base_notional_usdc, size_multiplier)
        if effective_notional_usdc <= 0:
            no_signal_count += 1
            no_signal_reasons["ENTRY_DISABLED_BY_POSITION_SIZE_MULTIPLIER"] += 1
            continue

        entry_fill = execution_model.simulate_entry_fill(
            decision=decision,
            direction=entry_direction,
            bars=bars,
            index=index,
            slippage_bps=slippage_bps,
            rng=rng,
        )
        if isinstance(entry_fill, RejectedEntry):
            no_signal_count += 1
            no_signal_reasons[entry_fill.reason] += 1
            continue

        resolved_entry_price = entry_fill.price
        if resolved_entry_price <= 0:
            no_signal_count += 1
            no_signal_reasons["INVALID_ENTRY_FILL_PRICE"] += 1
            continue

        quantity_sol = effective_notional_usdc / resolved_entry_price
        if quantity_sol <= 0:
            no_signal_count += 1
            no_signal_reasons["INVALID_ENTRY_QUANTITY"] += 1
            continue

        if strategy_bundle is not None:
            stop_candidate = strategy_bundle.stop_policy.compute_initial_stop(
                decision=decision,
                direction=entry_direction,
                entry_price=resolved_entry_price,
                max_loss_per_trade_pct=max_loss_per_trade_pct,
                config=config,
            )
            if stop_candidate is None:
                no_signal_count += 1
                no_signal_reasons["INVALID_RISK_AFTER_FILL"] += 1
                continue
            final_stop = stop_candidate
            if entry_direction == "LONG":
                take_profit_price = calculate_take_profit_price(
                    resolved_entry_price, final_stop, take_profit_r_multiple
                )
            else:
                take_profit_price = calculate_take_profit_price_for_short(
                    resolved_entry_price, final_stop, take_profit_r_multiple
                )
        else:
            swing_stop = float(decision.stop_price)
            if entry_direction == "LONG":
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

        atr_at_entry = 0.0
        diagnostics = getattr(decision, "diagnostics", None)
        if isinstance(diagnostics, dict):
            raw_atr = diagnostics.get("atr")
            if isinstance(raw_atr, (int, float)) and raw_atr > 0:
                atr_at_entry = float(raw_atr)

        enter_count += 1
        open_position = _OpenPosition(
            entry_index=entry_fill.bar_index,
            entry_time=entry_fill.fill_time,
            direction=entry_direction,
            quantity_sol=quantity_sol,
            entry_price=resolved_entry_price,
            stop_price=final_stop,
            take_profit_price=take_profit_price,
            position_size_multiplier=size_multiplier,
            base_notional_usdc=base_notional_usdc,
            effective_notional_usdc=effective_notional_usdc,
            entry_regime=get_bar_regime(bars[entry_fill.bar_index]),
            execution_model_id=execution_model.model_id,
            execution_seed=execution_seed,
            initial_stop_price=final_stop,
            initial_take_profit_price=take_profit_price,
            atr_at_entry=atr_at_entry,
            initial_quantity_sol=quantity_sol,
            initial_effective_notional_usdc=effective_notional_usdc,
            initial_base_notional_usdc=base_notional_usdc,
            remaining_fraction=1.0,
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
                entry_regime=dict(open_position.entry_regime),
                execution_model_id=open_position.execution_model_id,
                execution_seed=open_position.execution_seed,
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
            execution_model_id=execution_model.model_id,
            execution_seed=execution_seed,
        ),
        no_signal_reason_counts=dict(no_signal_reasons),
        trades=trades,
    )
    return report
