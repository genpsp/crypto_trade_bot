from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from pybot.app.ports.execution_port import ExecutionPort
from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort
from pybot.app.ports.market_data_port import MarketDataPort
from pybot.app.ports.persistence_port import PersistencePort
from pybot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
)
from pybot.app.usecases.open_position import (
    OpenPositionDependencies,
    OpenPositionInput,
    open_position,
)
from pybot.app.usecases.usecase_utils import to_error_message
from pybot.domain.model.types import BotConfig, ModelConfig, RunRecord
from pybot.domain.strategy.registry import evaluate_strategy_for_model
from pybot.domain.utils.math import round_to
from pybot.domain.utils.time import build_run_id, get_last_closed_bar_close, get_utc_day_range

RUN_LOCK_TTL_SECONDS = 240
ENTRY_IDEM_TTL_SECONDS = 12 * 60 * 60
OHLCV_LIMIT = 300


@dataclass
class RunCycleDependencies:
    execution: ExecutionPort
    lock: LockPort
    logger: LoggerPort
    market_data: MarketDataPort
    persistence: PersistencePort
    model_id: str
    now_provider: Callable[[], datetime] | None = None


def _round_metric(value: Any, digits: int = 6) -> float | None:
    if isinstance(value, (int, float)):
        return round_to(float(value), digits)
    return None


def _find_model(config: BotConfig, model_id: str) -> ModelConfig | None:
    for model in config.get("models", []):
        if model.get("model_id") == model_id:
            return model
    return None


def _to_runtime_config(config: BotConfig, model: ModelConfig) -> BotConfig:
    runtime: BotConfig = {
        **config,
        "direction": model["direction"],
        "strategy": model["strategy"],
        "risk": model["risk"],
        "exit": model["exit"],
    }
    return runtime


def _build_model_run_id(model_id: str, bar_close_time_iso: str, run_at: datetime) -> str:
    return f"{model_id}_{build_run_id(bar_close_time_iso, run_at)}"


def run_cycle(dependencies: RunCycleDependencies) -> RunRecord:
    execution = dependencies.execution
    lock = dependencies.lock
    logger = dependencies.logger
    market_data = dependencies.market_data
    persistence = dependencies.persistence
    model_id = dependencies.model_id
    now_provider = dependencies.now_provider

    run_at = now_provider() if now_provider else datetime.now(tz=UTC)
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

    locked = lock.acquire_runner_lock(RUN_LOCK_TTL_SECONDS)
    if not locked:
        run["result"] = "SKIPPED"
        run["summary"] = "SKIPPED: lock:runner already acquired by another process"
        persistence.save_run(run)
        return run

    try:
        config = persistence.get_current_config()
        model = _find_model(config, model_id)
        if model is None:
            run["result"] = "SKIPPED"
            run["summary"] = f"SKIPPED: model {model_id} is not found in config.models"
            return run
        if not model["enabled"]:
            run["result"] = "SKIPPED"
            run["summary"] = f"SKIPPED: model {model_id} is disabled"
            return run

        runtime_config = _to_runtime_config(config, model)

        timeframe = runtime_config["signal_timeframe"]
        bar_close_time = get_last_closed_bar_close(run_at, timeframe)
        bar_close_time_iso = bar_close_time.isoformat().replace("+00:00", "Z")
        run["run_id"] = _build_model_run_id(model_id, bar_close_time_iso, run_at)
        run["bar_close_time_iso"] = bar_close_time_iso
        run["config_version"] = runtime_config["meta"]["config_version"]

        if not runtime_config["enabled"]:
            run["result"] = "SKIPPED"
            run["summary"] = "SKIPPED: model config.enabled is false"
            return run

        open_trade = persistence.find_open_trade(runtime_config["pair"])
        if open_trade:
            run["trade_id"] = open_trade["trade_id"]
            mark_price = execution.get_mark_price(runtime_config["pair"])
            trigger_reason = "NONE"
            trade_direction = open_trade.get("direction", runtime_config["direction"])
            stop_price = open_trade["position"]["stop_price"]
            take_profit_price = open_trade["position"]["take_profit_price"]

            if trade_direction == "LONG_ONLY":
                if mark_price >= take_profit_price:
                    trigger_reason = "TAKE_PROFIT"
                elif mark_price <= stop_price:
                    trigger_reason = "STOP_LOSS"
            else:
                if mark_price <= take_profit_price:
                    trigger_reason = "TAKE_PROFIT"
                elif mark_price >= stop_price:
                    trigger_reason = "STOP_LOSS"

            run["metrics"] = {
                "phase": "EXIT_CHECK",
                "model_id": model_id,
                "direction": trade_direction,
                "mark_price": round_to(mark_price, 6),
                "entry_price": _round_metric(open_trade.get("position", {}).get("entry_price")),
                "stop_price": _round_metric(stop_price),
                "take_profit_price": _round_metric(take_profit_price),
                "quantity_sol": _round_metric(open_trade.get("position", {}).get("quantity_sol"), 9),
                "trigger_reason": trigger_reason,
                "bar_close_time_iso": bar_close_time_iso,
            }

            logger.info(
                "exit check",
                {
                    "model_id": model_id,
                    "direction": trade_direction,
                    "markPrice": round_to(mark_price, 6),
                    "stop": round_to(stop_price, 6),
                    "tp": round_to(take_profit_price, 6),
                    "triggerReason": trigger_reason,
                },
            )

            if trigger_reason in ("TAKE_PROFIT", "STOP_LOSS"):
                closed = close_position(
                    ClosePositionDependencies(execution=execution, lock=lock, logger=logger, persistence=persistence),
                    ClosePositionInput(
                        config=runtime_config,
                        trade=open_trade,
                        close_reason=trigger_reason,
                        close_price=mark_price,
                    ),
                )
                run["result"] = "CLOSED" if closed.status == "CLOSED" else "FAILED"
                run["summary"] = closed.summary
                return run

            run["result"] = "HOLD"
            run["summary"] = "HOLD: open position exists and no exit trigger fired on this bar"
            return run

        already_judged = lock.has_entry_attempt(bar_close_time_iso)
        if already_judged:
            run["result"] = "SKIPPED_ENTRY"
            run["summary"] = "SKIPPED_ENTRY: entry already evaluated for this bar"
            run["metrics"] = {
                "phase": "ENTRY_CHECK",
                "model_id": model_id,
                "bar_close_time_iso": bar_close_time_iso,
                "entry_idem": "already_judged",
            }
            return run

        bars = market_data.fetch_bars(runtime_config["pair"], timeframe, OHLCV_LIMIT)
        closed_bars = [bar for bar in bars if bar.close_time <= bar_close_time]
        latest_closed_bar = closed_bars[-1] if closed_bars else None
        if latest_closed_bar is None:
            run["result"] = "FAILED"
            run["summary"] = "FAILED: no closed bars available"
            return run

        if latest_closed_bar.close_time != bar_close_time:
            run["result"] = "FAILED"
            run["summary"] = f"FAILED: market bar close does not match expected {timeframe} close"
            run["reason"] = (
                f"EXPECTED_{bar_close_time.isoformat().replace('+00:00', 'Z')}"
                f"_GOT_{latest_closed_bar.close_time.isoformat().replace('+00:00', 'Z')}"
            )
            return run

        day_start_iso, day_end_iso = get_utc_day_range(bar_close_time)
        trades_today = persistence.count_trades_for_utc_day(runtime_config["pair"], day_start_iso, day_end_iso)
        run["metrics"] = {
            "phase": "ENTRY_CHECK",
            "model_id": model_id,
            "direction": runtime_config["direction"],
            "bar_close_price": round_to(latest_closed_bar.close, 6),
            "bar_close_time_iso": bar_close_time_iso,
            "trades_today": trades_today,
            "max_trades_per_day": runtime_config["risk"]["max_trades_per_day"],
        }
        if trades_today >= runtime_config["risk"]["max_trades_per_day"]:
            run["result"] = "SKIPPED"
            run["summary"] = "SKIPPED: max_trades_per_day reached"
            run["reason"] = f"TRADES_TODAY_{trades_today}"
            return run

        decision = evaluate_strategy_for_model(
            direction=runtime_config["direction"],
            bars=closed_bars,
            strategy=runtime_config["strategy"],
            risk=runtime_config["risk"],
            exit=runtime_config["exit"],
            execution=runtime_config["execution"],
        )
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
                "diagnostics": decision.diagnostics,
            },
        )
        diagnostics = decision.diagnostics or {}
        run["metrics"] = {
            "phase": "ENTRY_CHECK",
            "model_id": model_id,
            "direction": runtime_config["direction"],
            "bar_close_price": round_to(latest_closed_bar.close, 6),
            "bar_close_time_iso": bar_close_time_iso,
            "decision_type": decision.type,
            "ema_fast": _round_metric(decision.ema_fast),
            "ema_slow": _round_metric(decision.ema_slow),
            "entry_price": _round_metric(decision.entry_price) if decision.type == "ENTER" else None,
            "stop_price": _round_metric(decision.stop_price) if decision.type == "ENTER" else None,
            "take_profit_price": _round_metric(decision.take_profit_price) if decision.type == "ENTER" else None,
            "rsi": _round_metric(diagnostics.get("rsi"), 4),
            "atr": _round_metric(diagnostics.get("atr"), 6),
            "atr_pct": _round_metric(diagnostics.get("atr_pct"), 4),
            "distance_from_ema_fast_pct": _round_metric(
                diagnostics.get("distance_from_ema_fast_pct"),
                4,
            ),
            "stop_distance_pct": _round_metric(diagnostics.get("stop_distance_pct"), 4),
            "volatility_regime": diagnostics.get("volatility_regime"),
            "position_size_multiplier": _round_metric(
                diagnostics.get("position_size_multiplier"),
                4,
            ),
            "reason": decision.reason if decision.type == "NO_SIGNAL" else None,
        }

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
            run["metrics"] = {
                "phase": "ENTRY_CHECK",
                "model_id": model_id,
                "bar_close_time_iso": bar_close_time_iso,
                "entry_idem": "already_marked",
            }
            return run

        opened = open_position(
            OpenPositionDependencies(execution=execution, lock=lock, logger=logger, persistence=persistence),
            OpenPositionInput(
                config=runtime_config,
                signal=decision,
                bar_close_time_iso=bar_close_time_iso,
                model_id=model_id,
            ),
        )
        run["trade_id"] = opened.trade_id
        run["result"] = "OPENED" if opened.status == "OPENED" else "FAILED"
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
