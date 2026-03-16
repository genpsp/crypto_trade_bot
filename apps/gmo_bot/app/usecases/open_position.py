from __future__ import annotations

from dataclasses import dataclass
import math

from apps.dex_bot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_max_loss_stop_price_for_short,
    calculate_take_profit_price,
    calculate_take_profit_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from apps.gmo_bot.app.ports.execution_port import ExecutionPort, SubmitEntryOrderRequest
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.protective_exit_orders import (
    ArmProtectiveExitOrdersDependencies,
    ArmProtectiveExitOrdersInput,
    arm_protective_exit_orders,
)
from apps.gmo_bot.app.usecases.usecase_utils import now_iso, strip_none, summarize_error_for_log, to_error_message
from apps.gmo_bot.domain.model.trade_state import assert_trade_state_transition
from apps.gmo_bot.domain.model.types import BotConfig, Direction, EntrySignalDecision, TradeRecord, TradeState
from shared.utils.math import round_to
from apps.gmo_bot.domain.utils.time import build_trade_id

ORDER_CONFIRM_TIMEOUT_MS = 20_000


@dataclass
class OpenPositionInput:
    config: BotConfig
    signal: EntrySignalDecision
    bar_close_time_iso: str
    model_id: str
    entry_direction: Direction | None = None


@dataclass
class OpenPositionResult:
    status: str
    trade_id: str
    summary: str


@dataclass
class OpenPositionDependencies:
    execution: ExecutionPort
    lock: LockPort
    logger: LoggerPort
    persistence: PersistencePort


def _resolve_regime_and_multiplier(signal: EntrySignalDecision) -> tuple[str, float]:
    diagnostics = signal.diagnostics or {}
    volatility_regime = diagnostics.get("volatility_regime")
    if volatility_regime not in ("NORMAL", "VOLATILE", "STORM"):
        volatility_regime = "NORMAL"
    raw_multiplier = diagnostics.get("position_size_multiplier")
    multiplier = float(raw_multiplier) if isinstance(raw_multiplier, (int, float)) and raw_multiplier >= 0 else 1.0
    return str(volatility_regime), multiplier


def _round_down_to_step(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    scaled = math.floor(value / step)
    return round(scaled * step, 10)


def _build_plan_summary(
    direction: str,
    effective_notional_jpy: float,
    base_notional_jpy: float,
    volatility_regime: str,
    position_size_multiplier: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
) -> str:
    action = "Buy SOL" if direction == "LONG" else "Sell SOL"
    return (
        f"{action} with {round_to(effective_notional_jpy, 2)} JPY "
        f"(base={round_to(base_notional_jpy, 2)}, regime={volatility_regime}, size_x={position_size_multiplier:.2f}), "
        f"entry={round_to(entry_price, 4)}, stop={round_to(stop_price, 4)}, tp={round_to(take_profit_price, 4)}"
    )


def open_position(dependencies: OpenPositionDependencies, input_data: OpenPositionInput) -> OpenPositionResult:
    execution = dependencies.execution
    logger = dependencies.logger
    persistence = dependencies.persistence
    config = input_data.config
    signal = input_data.signal
    model_id = input_data.model_id
    direction = input_data.entry_direction or config["direction"]
    trade_id = build_trade_id(input_data.bar_close_time_iso, model_id, direction)
    now = now_iso()
    volatility_regime, position_size_multiplier = _resolve_regime_and_multiplier(signal)

    trade: TradeRecord = {
        "trade_id": trade_id,
        "model_id": model_id,
        "bar_close_time_iso": input_data.bar_close_time_iso,
        "pair": config["pair"],
        "direction": direction,
        "state": "CREATED",
        "config_version": config["meta"]["config_version"],
        "signal": {
            "summary": signal.summary,
            "bar_close_time_iso": input_data.bar_close_time_iso,
            "ema_fast": signal.ema_fast,
            "ema_slow": signal.ema_slow,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
        },
        "plan": {
            "summary": "",
            "notional_jpy": 0.0,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
            "r_multiple": config["exit"]["take_profit_r_multiple"],
        },
        "execution": {},
        "position": {
            "status": "CLOSED",
            "quantity_sol": 0.0,
            "quote_amount_jpy": 0.0,
            "entry_trigger_price": signal.entry_price,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
            "lots": [],
        },
        "created_at": now,
        "updated_at": now,
    }
    persistence.create_trade(trade)
    current_state: TradeState = trade["state"]  # type: ignore[assignment]

    def move_state(next_state: TradeState) -> None:
        nonlocal current_state
        assert_trade_state_transition(current_state, next_state)
        current_state = next_state
        trade["state"] = next_state
        trade["updated_at"] = now_iso()
        persistence.update_trade(
            trade_id,
            strip_none(
                {
                    "state": trade["state"],
                    "plan": trade["plan"],
                    "execution": trade["execution"],
                    "position": trade["position"],
                    "updated_at": trade["updated_at"],
                }
            ),
        )

    try:
        rule = execution.get_symbol_rule(config["pair"])
        available_margin_jpy = float(execution.get_available_margin_jpy())
        mark_price = float(execution.get_mark_price(config["pair"]))
        if mark_price <= 0:
            raise RuntimeError("mark price is invalid")

        base_notional_jpy = available_margin_jpy * float(config["execution"]["margin_usage_ratio"])
        base_notional_jpy *= float(config["execution"]["leverage_multiplier"])
        effective_notional_jpy = min(base_notional_jpy * position_size_multiplier, base_notional_jpy)
        if effective_notional_jpy < float(config["execution"]["min_notional_jpy"]):
            trade["execution"]["entry_error"] = (
                f"insufficient margin: {round_to(effective_notional_jpy, 2)} < min_notional_jpy "
                f"{config['execution']['min_notional_jpy']}"
            )
            move_state("FAILED")
            return OpenPositionResult(
                status="FAILED",
                trade_id=trade_id,
                summary=f"FAILED: {trade['execution']['entry_error']}",
            )

        target_size_sol = _round_down_to_step(effective_notional_jpy / mark_price, rule.size_step)
        if target_size_sol < rule.min_order_size:
            trade["execution"]["entry_error"] = "entry size rounded below min_order_size"
            move_state("CANCELED")
            return OpenPositionResult(
                status="CANCELED",
                trade_id=trade_id,
                summary="CANCELED: entry size rounded below min_order_size",
            )

        effective_notional_jpy = round_to(target_size_sol * mark_price, 2)
        trade["execution"]["entry_reference_price"] = round_to(mark_price, 6)
        trade["plan"] = {
            "summary": _build_plan_summary(
                direction,
                effective_notional_jpy,
                base_notional_jpy,
                volatility_regime,
                position_size_multiplier,
                signal.entry_price,
                signal.stop_price,
                signal.take_profit_price,
            ),
            "notional_jpy": effective_notional_jpy,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
            "r_multiple": config["exit"]["take_profit_r_multiple"],
        }

        submission = execution.submit_entry_order(
            SubmitEntryOrderRequest(
                side="BUY" if direction == "LONG" else "SELL",
                size_sol=target_size_sol,
                slippage_bps=int(config["execution"]["slippage_bps"]),
                reference_price=mark_price,
            )
        )
        trade["execution"]["entry_order_id"] = submission.order_id
        trade["execution"]["entry_submission_state"] = "SUBMITTED"
        if submission.order:
            trade["execution"]["entry_order"] = submission.order
        move_state("SUBMITTED")

        confirmation = execution.confirm_order(submission.order_id, ORDER_CONFIRM_TIMEOUT_MS)
        if not confirmation.confirmed or confirmation.result is None:
            trade["execution"]["entry_submission_state"] = "FAILED"
            trade["execution"]["entry_error"] = confirmation.error or "entry order not confirmed"
            move_state("FAILED")
            return OpenPositionResult(
                status="FAILED",
                trade_id=trade_id,
                summary=f"FAILED: {summarize_error_for_log(str(trade['execution']['entry_error']))}",
            )

        entry_result = confirmation.result
        trade["execution"]["entry_result"] = entry_result
        trade["execution"]["entry_submission_state"] = "CONFIRMED"
        fee_jpy = float(entry_result.get("fee_jpy") or 0.0)
        trade["execution"]["entry_fee_jpy"] = round_to(fee_jpy, 6)

        traded_base_sol = float(entry_result["filled_base_sol"])
        actual_quote_jpy = float(entry_result["filled_quote_jpy"])
        resolved_entry_price = float(entry_result["avg_fill_price"])
        lots = list(entry_result.get("lots") or [])
        if traded_base_sol <= 0 or actual_quote_jpy <= 0 or resolved_entry_price <= 0:
            raise RuntimeError("entry execution result is invalid")

        swing_stop = float(signal.stop_price)
        if direction == "LONG":
            pct_stop = calculate_max_loss_stop_price(resolved_entry_price, config["risk"]["max_loss_per_trade_pct"])
            final_stop = tighten_stop_for_long(
                resolved_entry_price,
                swing_stop,
                config["risk"]["max_loss_per_trade_pct"],
            )
            if final_stop >= resolved_entry_price:
                final_stop = pct_stop
            recalculated_take_profit = calculate_take_profit_price(
                resolved_entry_price,
                final_stop,
                config["exit"]["take_profit_r_multiple"],
            )
        else:
            pct_stop = calculate_max_loss_stop_price_for_short(
                resolved_entry_price,
                config["risk"]["max_loss_per_trade_pct"],
            )
            final_stop = tighten_stop_for_short(
                resolved_entry_price,
                swing_stop,
                config["risk"]["max_loss_per_trade_pct"],
            )
            if final_stop <= resolved_entry_price:
                final_stop = pct_stop
            recalculated_take_profit = calculate_take_profit_price_for_short(
                resolved_entry_price,
                final_stop,
                config["exit"]["take_profit_r_multiple"],
            )

        trade["position"]["quantity_sol"] = round_to(traded_base_sol, 9)
        trade["position"]["quote_amount_jpy"] = round_to(actual_quote_jpy, 6)
        trade["position"]["entry_price"] = round_to(resolved_entry_price, 6)
        trade["position"]["stop_price"] = round_to(final_stop, 6)
        trade["position"]["take_profit_price"] = round_to(recalculated_take_profit, 6)
        trade["position"]["entry_time_iso"] = now_iso()
        trade["position"]["status"] = "OPEN"
        trade["position"]["lots"] = lots
        trade["plan"]["entry_price"] = trade["position"]["entry_price"]
        trade["plan"]["stop_price"] = trade["position"]["stop_price"]
        trade["plan"]["take_profit_price"] = trade["position"]["take_profit_price"]
        trade["plan"]["summary"] = _build_plan_summary(
            direction,
            effective_notional_jpy,
            base_notional_jpy,
            volatility_regime,
            position_size_multiplier,
            trade["position"]["entry_price"],
            trade["position"]["stop_price"],
            trade["position"]["take_profit_price"],
        )
        move_state("CONFIRMED")
        protective_exit_result = arm_protective_exit_orders(
            ArmProtectiveExitOrdersDependencies(
                execution=execution,
                logger=logger,
                persistence=persistence,
            ),
            ArmProtectiveExitOrdersInput(config=config, trade=trade),
        )
        logger.info(
            "gmo trade opened",
            {
                "trade_id": trade_id,
                "model_id": model_id,
                "direction": direction,
                "order_id": submission.order_id,
                "entry_price": trade["position"]["entry_price"],
                "stop_price": trade["position"]["stop_price"],
                "take_profit_price": trade["position"]["take_profit_price"],
                "quantity_sol": trade["position"]["quantity_sol"],
            },
        )
        return OpenPositionResult(
            status="OPENED",
            trade_id=trade_id,
            summary=(
                f"OPENED: order_id={submission.order_id}, qty={trade['position']['quantity_sol']} SOL, "
                f"direction={direction}, protective_exits={protective_exit_result.status}"
            ),
        )
    except Exception as error:
        message = to_error_message(error)
        logger.error("gmo open_position failed", {"trade_id": trade_id, "error": message})
        trade["execution"]["entry_error"] = message
        if current_state in ("CREATED", "SUBMITTED"):
            move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {summarize_error_for_log(message)}",
        )
