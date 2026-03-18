from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from apps.dex_bot.domain.risk.loss_streak_trade_cap import LOSS_STREAK_LOOKBACK_CLOSED_TRADES
from apps.dex_bot.domain.risk.loss_streak_trade_cap import resolve_effective_max_trades_per_day_for_strategy
from apps.dex_bot.domain.risk.short_regime_guard import SHORT_REGIME_GUARD_REASON, resolve_short_regime_guard_state
from apps.dex_bot.domain.risk.short_stop_loss_cooldown import (
    SHORT_STOP_LOSS_COOLDOWN_REASON,
    resolve_short_stop_loss_cooldown_state,
)
from apps.gmo_bot.app.usecases.execution_error_classifier import classify_execution_error
from apps.gmo_bot.app.ports.execution_port import ExecutionPort
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.market_data_port import MarketDataPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
    reconcile_protective_exit_execution,
)
from apps.gmo_bot.app.usecases.open_position import OpenPositionDependencies, OpenPositionInput, open_position
from apps.gmo_bot.app.usecases.protective_exit_orders import (
    ArmProtectiveExitOrdersDependencies,
    ArmProtectiveExitOrdersInput,
    arm_protective_exit_orders,
    has_active_protective_exit_orders,
    has_active_stop_loss_order,
)
from apps.gmo_bot.app.usecases.usecase_utils import strip_none, to_error_message
from apps.gmo_bot.domain.model.types import BotConfig, Direction, ModelDirection, RunRecord, TradeRecord
from apps.gmo_bot.domain.strategy.registry import evaluate_strategy_for_model
from shared.utils.math import round_to
from apps.gmo_bot.domain.utils.time import build_run_id, get_bar_duration_seconds, get_jst_day_range, get_last_closed_bar_close

RUN_LOCK_TTL_SECONDS = 600
MIN_REQUIRED_RUN_LOCK_TTL_SECONDS = 480
ENTRY_IDEM_TTL_SECONDS = 12 * 60 * 60
DEFAULT_OHLCV_LIMIT = 300
OHLCV_LIMIT_FOR_15M_UPPER_TREND = 600
_EXECUTION_ERROR_SKIP_MARKERS = (
    "insufficient funds",
    "slippage exceeded",
    "route/liquidity unavailable",
    "entry execution skipped",
    "exit slippage exceeded",
    "exit route/liquidity unavailable",
)
_MARKET_DATA_MAINTENANCE_MARKERS = (
    "err-5201",
    "maintenance",
)
_MARK_PRICE_RATE_LIMIT_MARKERS = (
    "err-5003",
    "requests are too many",
)
TAKE_PROFIT_LATCH_MAX_PULLBACK_R = 0.15


@dataclass
class RunCycleDependencies:
    execution: ExecutionPort
    lock: LockPort
    logger: LoggerPort
    market_data: MarketDataPort
    persistence: PersistencePort
    model_id: str
    now_provider: Callable[[], datetime] | None = None
    prefetched_open_trade: TradeRecord | None = None
    use_prefetched_open_trade: bool = False


def _round_metric(value: Any, digits: int = 6) -> float | None:
    if isinstance(value, (int, float)):
        return round_to(float(value), digits)
    return None


def _build_model_run_id(model_id: str, bar_close_time_iso: str, run_at: datetime) -> str:
    return f"{model_id}_{build_run_id(bar_close_time_iso, run_at)}"


def _resolve_ohlcv_limit(config: BotConfig) -> int:
    if config["strategy"]["name"] == "ema_trend_pullback_15m_v0":
        return OHLCV_LIMIT_FOR_15M_UPPER_TREND
    return DEFAULT_OHLCV_LIMIT


def _resolve_effective_max_trades_per_day(*, runtime_config: BotConfig, recent_closed_trades: list[TradeRecord]) -> tuple[int, int, str]:
    base_max_trades_per_day = int(runtime_config["risk"]["max_trades_per_day"])
    recent_close_reasons = [trade.get("close_reason") for trade in recent_closed_trades]
    return resolve_effective_max_trades_per_day_for_strategy(
        strategy_name=runtime_config["strategy"]["name"],
        base_max_trades_per_day=base_max_trades_per_day,
        recent_close_reasons=recent_close_reasons,
    )


def _resolve_recent_closed_trades(*, persistence: PersistencePort, pair: str) -> list[TradeRecord]:
    return persistence.list_recent_closed_trades(pair, LOSS_STREAK_LOOKBACK_CLOSED_TRADES)


def _resolve_entry_direction(runtime_config: BotConfig, decision: Any) -> Direction | None:
    if decision.type != "ENTER":
        return None
    model_direction: ModelDirection = runtime_config["direction"]
    if model_direction in ("LONG", "SHORT"):
        return model_direction
    raw_entry_direction = (decision.diagnostics or {}).get("entry_direction")
    if raw_entry_direction in ("LONG", "SHORT"):
        return raw_entry_direction
    raise RuntimeError("BOTH model requires diagnostics.entry_direction to be LONG or SHORT on ENTER decision")


def _build_strategy_execution_bridge(runtime_config: BotConfig, reference_price: float) -> dict[str, Any]:
    del reference_price
    return {
        "min_notional_usdc": max(float(runtime_config["execution"]["min_notional_jpy"]), 1.0),
    }


def _is_execution_error_skip_summary(summary: str) -> bool:
    normalized = summary.strip().lower()
    if not normalized.startswith("skipped:"):
        return False
    if any(marker in normalized for marker in _EXECUTION_ERROR_SKIP_MARKERS):
        return True
    return classify_execution_error(normalized).action == "SKIP"


def _is_market_data_maintenance_error_message(message: str) -> bool:
    normalized = message.strip().lower()
    return all(marker in normalized for marker in _MARKET_DATA_MAINTENANCE_MARKERS)


def _is_market_data_maintenance_skip_summary(summary: str) -> bool:
    normalized = summary.strip().lower()
    return normalized.startswith("skipped: market data unavailable")


def _is_mark_price_rate_limit_error_message(message: str) -> bool:
    normalized = message.strip().lower()
    return all(marker in normalized for marker in _MARK_PRICE_RATE_LIMIT_MARKERS)


def _should_persist_run_record(run: RunRecord) -> bool:
    result = str(run.get("result") or "")
    if result in ("OPENED", "CLOSED", "PARTIALLY_CLOSED", "FAILED"):
        return True
    if result != "SKIPPED":
        return False
    summary = str(run.get("summary") or "")
    return _is_execution_error_skip_summary(summary) or _is_market_data_maintenance_skip_summary(summary)


def run_cycle(dependencies: RunCycleDependencies) -> RunRecord:
    execution = dependencies.execution
    lock = dependencies.lock
    logger = dependencies.logger
    market_data = dependencies.market_data
    persistence = dependencies.persistence
    model_id = dependencies.model_id
    run_at = dependencies.now_provider() if dependencies.now_provider else datetime.now(tz=UTC)
    run_at_iso = run_at.isoformat().replace("+00:00", "Z")
    provisional_bar_close_time_iso = run_at_iso
    run: RunRecord = {
        "run_id": _build_model_run_id(model_id, provisional_bar_close_time_iso, run_at),
        "model_id": model_id,
        "bar_close_time_iso": provisional_bar_close_time_iso,
        "executed_at_iso": run_at_iso,
        "result": "FAILED",
        "summary": "FAILED: run initialization",
    }

    if RUN_LOCK_TTL_SECONDS < MIN_REQUIRED_RUN_LOCK_TTL_SECONDS:
        raise RuntimeError(
            f"RUN_LOCK_TTL_SECONDS must be >= {MIN_REQUIRED_RUN_LOCK_TTL_SECONDS}, got {RUN_LOCK_TTL_SECONDS}"
        )

    locked = lock.acquire_runner_lock(RUN_LOCK_TTL_SECONDS)
    if not locked:
        run["result"] = "SKIPPED"
        run["summary"] = "SKIPPED: lock:runner already acquired by another process"
        persistence.save_run(run)
        return run

    try:
        runtime_config = persistence.get_current_config()
        if not runtime_config["enabled"]:
            run["result"] = "SKIPPED"
            run["summary"] = f"SKIPPED: model {model_id} is disabled"
            return run

        timeframe = runtime_config["signal_timeframe"]
        bar_close_time = get_last_closed_bar_close(run_at, timeframe)
        bar_close_time_iso = bar_close_time.isoformat().replace("+00:00", "Z")
        run["run_id"] = _build_model_run_id(model_id, bar_close_time_iso, run_at)
        run["bar_close_time_iso"] = bar_close_time_iso
        run["config_version"] = runtime_config["meta"]["config_version"]

        open_trade = dependencies.prefetched_open_trade if dependencies.use_prefetched_open_trade else persistence.find_open_trade(runtime_config["pair"])
        if open_trade:
            run["trade_id"] = open_trade["trade_id"]
            execution_snapshot = open_trade.get("execution", {})
            if not isinstance(execution_snapshot, dict):
                execution_snapshot = {}
                open_trade["execution"] = execution_snapshot
            if has_active_protective_exit_orders(open_trade):
                run["result"] = "HOLD"
                run["summary"] = "HOLD: protective exit orders are armed and managed by exchange"
                return run

            if not has_active_stop_loss_order(open_trade):
                protective_exit_result = arm_protective_exit_orders(
                    ArmProtectiveExitOrdersDependencies(
                        execution=execution,
                        logger=logger,
                        persistence=persistence,
                    ),
                    ArmProtectiveExitOrdersInput(config=runtime_config, trade=open_trade),
                )
                if protective_exit_result.status == "ARMED":
                    run["result"] = "HOLD"
                    run["summary"] = "HOLD: protective exit orders armed for existing open position"
                    return run

            try:
                mark_price = execution.get_mark_price(runtime_config["pair"])
            except Exception as error:
                error_message = to_error_message(error)
                if _is_mark_price_rate_limit_error_message(error_message):
                    run["result"] = "HOLD"
                    run["summary"] = "HOLD: market price temporarily unavailable (rate limit)"
                    run["reason"] = error_message
                    logger.warn("mark price temporarily unavailable due to GMO rate limit", {"model_id": model_id, "error": error_message})
                    return run
                raise
            trigger_reason = "NONE"
            trade_direction = open_trade.get("direction", runtime_config["direction"])
            stop_price = open_trade["position"]["stop_price"]
            take_profit_price = open_trade["position"]["take_profit_price"]
            entry_price = open_trade["position"].get("entry_price")
            take_profit_latched = isinstance(execution_snapshot.get("take_profit_triggered_at_iso"), str) and bool(
                str(execution_snapshot.get("take_profit_triggered_at_iso")).strip()
            )
            take_profit_trigger_price = execution_snapshot.get("take_profit_trigger_price")
            if trade_direction == "LONG":
                if mark_price >= take_profit_price:
                    trigger_reason = "TAKE_PROFIT"
                elif mark_price <= stop_price:
                    trigger_reason = "STOP_LOSS"
            else:
                if mark_price <= take_profit_price:
                    trigger_reason = "TAKE_PROFIT"
                elif mark_price >= stop_price:
                    trigger_reason = "STOP_LOSS"
            if trigger_reason == "TAKE_PROFIT" and not take_profit_latched:
                execution_snapshot["take_profit_triggered_at_iso"] = run_at_iso
                execution_snapshot["take_profit_trigger_price"] = round_to(mark_price, 6)
                persistence.update_trade(
                    open_trade["trade_id"],
                    strip_none({"execution": execution_snapshot, "updated_at": run_at_iso}),
                )
                logger.info(
                    "take profit trigger latched",
                    {
                        "model_id": model_id,
                        "trade_id": open_trade["trade_id"],
                        "trigger_price": round_to(mark_price, 6),
                    },
                )
                take_profit_latched = True
            elif trigger_reason == "NONE" and take_profit_latched:
                risk_distance = None
                if isinstance(entry_price, (int, float)):
                    risk_distance = abs(float(entry_price) - float(stop_price))
                elif runtime_config["exit"]["take_profit_r_multiple"] > 0:
                    risk_distance = abs(float(take_profit_price) - float(stop_price)) / (
                        float(runtime_config["exit"]["take_profit_r_multiple"]) + 1.0
                    )
                trigger_price_value = float(take_profit_trigger_price) if isinstance(take_profit_trigger_price, (int, float)) else None
                max_pullback = (
                    risk_distance * TAKE_PROFIT_LATCH_MAX_PULLBACK_R
                    if risk_distance is not None and risk_distance > 0
                    else None
                )
                latch_exceeded = False
                if trigger_price_value is not None and max_pullback is not None:
                    if trade_direction == "LONG":
                        latch_exceeded = mark_price < (trigger_price_value - max_pullback)
                    else:
                        latch_exceeded = mark_price > (trigger_price_value + max_pullback)
                if latch_exceeded:
                    execution_snapshot.pop("take_profit_triggered_at_iso", None)
                    execution_snapshot.pop("take_profit_trigger_price", None)
                    persistence.update_trade(
                        open_trade["trade_id"],
                        strip_none({"execution": execution_snapshot, "updated_at": run_at_iso}),
                    )
                    logger.info(
                        "take profit trigger latch cleared",
                        {
                            "model_id": model_id,
                            "trade_id": open_trade["trade_id"],
                            "mark_price": round_to(mark_price, 6),
                            "trigger_price": round_to(trigger_price_value, 6),
                            "max_pullback": round_to(max_pullback, 6),
                        },
                    )
                    take_profit_latched = False
                else:
                    trigger_reason = "TAKE_PROFIT"
            logger.info(
                "exit check",
                {
                    "model_id": model_id,
                    "direction": str(trade_direction),
                    "markPrice": round_to(mark_price, 6),
                    "stop": round_to(stop_price, 6),
                    "tp": round_to(take_profit_price, 6),
                    "triggerReason": trigger_reason,
                    "takeProfitLatched": take_profit_latched,
                },
            )
            if trigger_reason in ("TAKE_PROFIT", "STOP_LOSS"):
                if trigger_reason == "STOP_LOSS" and has_active_stop_loss_order(open_trade):
                    reconciled = reconcile_protective_exit_execution(
                        execution=execution,
                        logger=logger,
                        persistence=persistence,
                        trade=open_trade,
                        close_reason="STOP_LOSS",
                    )
                    if reconciled.close_result is not None:
                        run["result"] = (
                            reconciled.close_result.status
                            if reconciled.close_result.status in ("CLOSED", "PARTIALLY_CLOSED")
                            else "FAILED"
                        )
                        run["summary"] = reconciled.close_result.summary
                        return run
                    if reconciled.status == "PENDING":
                        run["result"] = "HOLD"
                        run["summary"] = "HOLD: protective stop order triggered and exchange execution is pending"
                        return run
                closed = close_position(
                    ClosePositionDependencies(execution=execution, lock=lock, logger=logger, persistence=persistence),
                    ClosePositionInput(
                        config=runtime_config,
                        trade=open_trade,
                        close_reason=trigger_reason,
                        close_price=mark_price,
                    ),
                )
                if closed.status in ("CLOSED", "PARTIALLY_CLOSED"):
                    run["result"] = closed.status
                elif closed.status == "PENDING":
                    run["result"] = "HOLD"
                else:
                    run["result"] = "FAILED"
                run["summary"] = closed.summary
                return run
            run["result"] = "HOLD"
            run["summary"] = "HOLD: open position exists and no exit trigger fired on this bar"
            return run

        already_judged = lock.has_entry_attempt(bar_close_time_iso)
        if already_judged:
            run["result"] = "SKIPPED_ENTRY"
            run["summary"] = "SKIPPED_ENTRY: entry already evaluated for this bar"
            return run

        ohlcv_limit = _resolve_ohlcv_limit(runtime_config)
        try:
            bars = market_data.fetch_bars(runtime_config["pair"], timeframe, ohlcv_limit)
        except Exception as error:
            error_message = to_error_message(error)
            if _is_market_data_maintenance_error_message(error_message):
                run["result"] = "SKIPPED"
                run["summary"] = "SKIPPED: market data unavailable (maintenance)"
                run["reason"] = error_message
                logger.warn("market data maintenance detected", {"model_id": model_id, "error": error_message})
                return run
            raise
        closed_bars = [bar for bar in bars if bar.close_time <= bar_close_time]
        latest_closed_bar = closed_bars[-1] if closed_bars else None
        if latest_closed_bar is None:
            run["result"] = "FAILED"
            run["summary"] = "FAILED: no closed bars available"
            return run
        if latest_closed_bar.close_time != bar_close_time:
            run["result"] = "SKIPPED"
            run["summary"] = f"SKIPPED: latest market bar is behind expected {timeframe} close"
            run["reason"] = (
                f"EXPECTED_{bar_close_time.isoformat().replace('+00:00', 'Z')}"
                f"_GOT_{latest_closed_bar.close_time.isoformat().replace('+00:00', 'Z')}"
            )
            return run

        day_start_iso, day_end_iso = get_jst_day_range(bar_close_time)
        trades_today = persistence.count_trades_for_utc_day(runtime_config["pair"], day_start_iso, day_end_iso)
        recent_closed_trades = _resolve_recent_closed_trades(persistence=persistence, pair=runtime_config["pair"])
        effective_max_trades_per_day, consecutive_loss_streak, dynamic_cap_reason = _resolve_effective_max_trades_per_day(
            runtime_config=runtime_config,
            recent_closed_trades=recent_closed_trades,
        )
        short_cooldown_active, short_cooldown_bars_since, short_cooldown_remaining_bars = resolve_short_stop_loss_cooldown_state(
            strategy_name=runtime_config["strategy"]["name"],
            recent_closed_trades=recent_closed_trades,
            current_bar_close_time=bar_close_time,
            bar_duration_seconds=get_bar_duration_seconds(timeframe),
        )
        (
            short_regime_guard_active,
            short_regime_guard_consecutive_stop_losses,
            short_regime_guard_remaining_bars,
            short_regime_guard_recent_short_trades,
            short_regime_guard_recent_short_win_rate_pct,
        ) = resolve_short_regime_guard_state(
            strategy_name=runtime_config["strategy"]["name"],
            recent_closed_trades=recent_closed_trades,
            current_bar_close_time=bar_close_time,
            bar_duration_seconds=get_bar_duration_seconds(timeframe),
        )
        if trades_today >= effective_max_trades_per_day:
            run["result"] = "SKIPPED"
            run["summary"] = "SKIPPED: max_trades_per_day reached"
            run["reason"] = (
                f"TRADES_TODAY_{trades_today}_CAP_{effective_max_trades_per_day}_"
                f"LOSS_STREAK_{consecutive_loss_streak}_{dynamic_cap_reason}"
            )
            return run

        decision = evaluate_strategy_for_model(
            direction=runtime_config["direction"],
            bars=closed_bars,
            strategy=runtime_config["strategy"],
            risk=runtime_config["risk"],
            exit=runtime_config["exit"],
            execution=_build_strategy_execution_bridge(runtime_config, latest_closed_bar.close),
        )
        entry_direction = _resolve_entry_direction(runtime_config, decision)
        logger.info(
            "strategy evaluation",
            {
                "model_id": model_id,
                "bar_close_time_iso": bar_close_time_iso,
                "decision_type": decision.type,
                "summary": decision.summary,
                "reason": decision.reason if decision.type == "NO_SIGNAL" else None,
                "ema_fast": decision.ema_fast,
                "ema_slow": decision.ema_slow,
                "entry_price": decision.entry_price if decision.type == "ENTER" else None,
                "stop_price": decision.stop_price if decision.type == "ENTER" else None,
                "take_profit_price": decision.take_profit_price if decision.type == "ENTER" else None,
                "entry_direction": entry_direction if decision.type == "ENTER" else None,
                "diagnostics": decision.diagnostics,
            },
        )
        if decision.type == "ENTER" and entry_direction == "SHORT" and short_cooldown_active:
            lock.mark_entry_attempt(bar_close_time_iso, ENTRY_IDEM_TTL_SECONDS)
            run["result"] = "NO_SIGNAL"
            run["summary"] = "NO_SIGNAL: short cooldown after stop-loss is active"
            run["reason"] = SHORT_STOP_LOSS_COOLDOWN_REASON
            return run
        if decision.type == "ENTER" and entry_direction == "SHORT" and short_regime_guard_active:
            lock.mark_entry_attempt(bar_close_time_iso, ENTRY_IDEM_TTL_SECONDS)
            run["result"] = "NO_SIGNAL"
            run["summary"] = "NO_SIGNAL: short regime guard is active"
            run["reason"] = SHORT_REGIME_GUARD_REASON
            return run
        if decision.type == "NO_SIGNAL":
            lock.mark_entry_attempt(bar_close_time_iso, ENTRY_IDEM_TTL_SECONDS)
            run["result"] = "NO_SIGNAL"
            run["summary"] = decision.summary
            run["reason"] = decision.reason
            return run
        marked = lock.mark_entry_attempt(bar_close_time_iso, ENTRY_IDEM_TTL_SECONDS)
        if not marked:
            run["result"] = "SKIPPED_ENTRY"
            run["summary"] = "SKIPPED_ENTRY: idem entry key already exists for this bar"
            return run
        opened = open_position(
            OpenPositionDependencies(execution=execution, lock=lock, logger=logger, persistence=persistence),
            OpenPositionInput(
                config=runtime_config,
                signal=decision,
                bar_close_time_iso=bar_close_time_iso,
                model_id=model_id,
                entry_direction=entry_direction,
            ),
        )
        run["trade_id"] = opened.trade_id
        if opened.status == "OPENED":
            run["result"] = "OPENED"
        elif opened.status == "CANCELED":
            run["result"] = "SKIPPED_ENTRY"
        else:
            run["result"] = "FAILED"
        run["summary"] = opened.summary
        return run
    except Exception as error:
        error_message = to_error_message(error)
        run["result"] = "FAILED"
        run["summary"] = "FAILED: unhandled run_cycle error"
        run["reason"] = error_message
        logger.error("run_cycle unhandled error", {"model_id": model_id, "error": error_message})
        return run
    finally:
        try:
            if _should_persist_run_record(run):
                persistence.save_run(run)
        except Exception as save_error:
            logger.error(
                "failed to save run record",
                {
                    "error": to_error_message(save_error),
                    "run_id": run.get("run_id"),
                    "model_id": model_id,
                },
            )
        lock.release_runner_lock()
