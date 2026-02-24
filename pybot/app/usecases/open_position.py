from __future__ import annotations

from dataclasses import dataclass
import time

from pybot.app.ports.execution_port import ExecutionPort, SubmitSwapRequest, SwapSide
from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort
from pybot.app.ports.persistence_port import PersistencePort
from pybot.app.usecases.usecase_utils import (
    now_iso,
    should_retry_error,
    strip_none,
    to_error_message,
)
from pybot.domain.model.trade_state import assert_trade_state_transition
from pybot.domain.model.types import BotConfig, EntrySignalDecision, TradeRecord, TradeState
from pybot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_max_loss_stop_price_for_short,
    calculate_take_profit_price,
    calculate_take_profit_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from pybot.domain.utils.math import round_to
from pybot.domain.utils.time import build_trade_id

USDC_ATOMIC_MULTIPLIER = 1_000_000
SOL_ATOMIC_MULTIPLIER = 1_000_000_000
TX_CONFIRM_TIMEOUT_MS = 75_000
TX_INFLIGHT_TTL_SECONDS = 180
SOL_FEE_RESERVE = 0.02
ENTRY_RETRY_ATTEMPTS = 3
ENTRY_RETRY_DELAY_SECONDS = 0.4


@dataclass
class OpenPositionInput:
    config: BotConfig
    signal: EntrySignalDecision
    bar_close_time_iso: str
    model_id: str


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
    volatility_regime = "NORMAL"
    raw_regime = diagnostics.get("volatility_regime")
    if isinstance(raw_regime, str) and raw_regime in ("NORMAL", "VOLATILE", "STORM"):
        volatility_regime = raw_regime

    position_size_multiplier = 1.0
    raw_multiplier = diagnostics.get("position_size_multiplier")
    if isinstance(raw_multiplier, (int, float)) and raw_multiplier >= 0:
        position_size_multiplier = float(raw_multiplier)

    return volatility_regime, position_size_multiplier


def _build_plan_summary(
    direction: str,
    effective_notional_usdc: float,
    base_notional_usdc: float,
    volatility_regime: str,
    position_size_multiplier: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
) -> str:
    action = "Buy SOL" if direction == "LONG_ONLY" else "Sell SOL"
    return (
        f"{action} with {effective_notional_usdc} USDC "
        f"(base={base_notional_usdc}, regime={volatility_regime}, size_x={position_size_multiplier:.2f}), "
        f"entry={round_to(entry_price, 4)}, "
        f"stop={round_to(stop_price, 4)}, "
        f"tp={round_to(take_profit_price, 4)}"
    )


def open_position(dependencies: OpenPositionDependencies, input_data: OpenPositionInput) -> OpenPositionResult:
    execution = dependencies.execution
    lock = dependencies.lock
    logger = dependencies.logger
    persistence = dependencies.persistence
    config = input_data.config
    signal = input_data.signal
    bar_close_time_iso = input_data.bar_close_time_iso
    model_id = input_data.model_id

    direction = config["direction"]
    trade_id = build_trade_id(bar_close_time_iso, model_id, direction)
    now = now_iso()
    configured_min_notional_usdc = float(config["execution"]["min_notional_usdc"])
    volatility_regime, position_size_multiplier = _resolve_regime_and_multiplier(signal)

    balance_error: str | None = None
    mark_price = signal.entry_price
    base_notional_usdc = 0.0

    try:
        if direction == "LONG_ONLY":
            available_quote_usdc = float(execution.get_available_quote_usdc(config["pair"]))
            base_notional_usdc = round_to(available_quote_usdc, 6)
        else:
            available_base_sol = float(execution.get_available_base_sol(config["pair"]))
            shortable_sol = max(available_base_sol - SOL_FEE_RESERVE, 0.0)
            mark_price = float(execution.get_mark_price(config["pair"]))
            if mark_price <= 0:
                raise RuntimeError("mark price for short model is invalid")
            base_notional_usdc = round_to(shortable_sol * mark_price, 6)
    except Exception as error:
        balance_error = f"failed to fetch balance for {direction}: {to_error_message(error)}"
        logger.error(
            "failed to fetch entry balance",
            {"pair": config["pair"], "direction": direction, "error": to_error_message(error)},
        )

    effective_notional_usdc = round_to(base_notional_usdc * position_size_multiplier, 2)

    trade: TradeRecord = {
        "trade_id": trade_id,
        "model_id": model_id,
        "bar_close_time_iso": bar_close_time_iso,
        "pair": config["pair"],
        "direction": direction,
        "state": "CREATED",
        "config_version": config["meta"]["config_version"],
        "signal": {
            "summary": signal.summary,
            "bar_close_time_iso": bar_close_time_iso,
            "ema_fast": signal.ema_fast,
            "ema_slow": signal.ema_slow,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
        },
        "plan": {
            "summary": _build_plan_summary(
                direction,
                effective_notional_usdc,
                base_notional_usdc,
                volatility_regime,
                position_size_multiplier,
                signal.entry_price,
                signal.stop_price,
                signal.take_profit_price,
            ),
            "notional_usdc": effective_notional_usdc,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
            "r_multiple": config["exit"]["take_profit_r_multiple"],
        },
        "execution": {},
        "position": {
            "status": "OPEN",
            "quantity_sol": 0.0,
            "quote_amount_usdc": 0.0,
            "entry_trigger_price": signal.entry_price,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit_price": signal.take_profit_price,
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
            trade["trade_id"],
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

    def persist_execution_only() -> None:
        trade["updated_at"] = now_iso()
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": trade["execution"], "updated_at": trade["updated_at"]}),
        )

    if configured_min_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "min_notional_usdc must be > 0"
        move_state("FAILED")
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary="FAILED: invalid min_notional_usdc")

    if balance_error:
        trade["execution"]["entry_error"] = balance_error
        move_state("FAILED")
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {balance_error}")

    if base_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "available balance is 0"
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    if base_notional_usdc < configured_min_notional_usdc:
        trade["execution"]["entry_error"] = (
            f"insufficient balance: {base_notional_usdc} < min_notional_usdc "
            f"{configured_min_notional_usdc}"
        )
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    if effective_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "entry disabled by position_size_multiplier=0"
        move_state("CANCELED")
        return OpenPositionResult(
            status="CANCELED",
            trade_id=trade_id,
            summary=f"CANCELED: {trade['execution']['entry_error']}",
        )

    entry_side: SwapSide = "BUY_SOL_WITH_USDC"
    amount_atomic = int(round(effective_notional_usdc * USDC_ATOMIC_MULTIPLIER))
    if direction == "SHORT_ONLY":
        sell_quantity_sol = effective_notional_usdc / mark_price
        amount_atomic = int(sell_quantity_sol * SOL_ATOMIC_MULTIPLIER)
        entry_side = "SELL_SOL_FOR_USDC"

    if amount_atomic <= 0:
        trade["execution"]["entry_error"] = "entry amount_atomic must be > 0"
        move_state("FAILED")
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {trade['execution']['entry_error']}")

    confirmed_submission = None
    confirmed_entry_result = None
    max_entry_attempts = ENTRY_RETRY_ATTEMPTS
    last_error_message = "unknown entry error"

    for attempt in range(1, max_entry_attempts + 1):
        try:
            submission = execution.submit_swap(
                SubmitSwapRequest(
                    side=entry_side,
                    amount_atomic=amount_atomic,
                    slippage_bps=config["execution"]["slippage_bps"],
                    only_direct_routes=config["execution"]["only_direct_routes"],
                )
            )

            trade["execution"]["entry_tx_signature"] = submission.tx_signature
            if submission.order:
                trade["execution"]["entry_order"] = submission.order
                trade["execution"]["order"] = submission.order

            entry_result = submission.result
            if entry_result is None:
                if entry_side == "BUY_SOL_WITH_USDC":
                    estimated_spent_quote_usdc = submission.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
                    estimated_filled_base_sol = submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
                else:
                    estimated_spent_quote_usdc = submission.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
                    estimated_filled_base_sol = submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
                estimated_avg_fill_price = (
                    estimated_spent_quote_usdc / estimated_filled_base_sol
                    if estimated_filled_base_sol > 0
                    else 0
                )
                entry_result = {
                    "status": "ESTIMATED",
                    "avg_fill_price": estimated_avg_fill_price,
                    "spent_quote_usdc": estimated_spent_quote_usdc,
                    "filled_base_sol": estimated_filled_base_sol,
                }
            trade["execution"]["entry_result"] = entry_result
            trade["execution"]["result"] = entry_result
            if "entry_error" in trade["execution"]:
                del trade["execution"]["entry_error"]

            if current_state == "CREATED":
                move_state("SUBMITTED")
            else:
                persist_execution_only()

            lock.set_inflight_tx(submission.tx_signature, TX_INFLIGHT_TTL_SECONDS)
            try:
                confirmation = execution.confirm_swap(submission.tx_signature, TX_CONFIRM_TIMEOUT_MS)
            finally:
                lock.clear_inflight_tx(submission.tx_signature)

            if not confirmation.confirmed:
                last_error_message = confirmation.error or "unknown confirmation error"
                trade["execution"]["entry_error"] = f"attempt {attempt}/{max_entry_attempts}: {last_error_message}"
                persist_execution_only()
                if should_retry_error(
                    attempt=attempt,
                    max_attempts=max_entry_attempts,
                    error_message=last_error_message,
                ):
                    logger.warn(
                        "open_position retrying after unconfirmed entry tx",
                        {
                            "trade_id": trade_id,
                            "attempt": attempt,
                            "max_attempts": max_entry_attempts,
                            "error": last_error_message,
                        },
                    )
                    time.sleep(ENTRY_RETRY_DELAY_SECONDS)
                    continue

                move_state("FAILED")
                return OpenPositionResult(
                    status="FAILED",
                    trade_id=trade_id,
                    summary=f"FAILED: entry tx not confirmed ({last_error_message})",
                )

            confirmed_submission = submission
            confirmed_entry_result = entry_result
            break
        except Exception as error:
            last_error_message = to_error_message(error)
            trade["execution"]["entry_error"] = f"attempt {attempt}/{max_entry_attempts}: {last_error_message}"
            try:
                persist_execution_only()
            except Exception as persist_error:
                logger.error(
                    "open_position execution persistence failed",
                    {"trade_id": trade_id, "error": to_error_message(persist_error)},
                )

            if should_retry_error(
                attempt=attempt,
                max_attempts=max_entry_attempts,
                error_message=last_error_message,
            ):
                logger.warn(
                    "open_position retrying after submit/confirm exception",
                    {
                        "trade_id": trade_id,
                        "attempt": attempt,
                        "max_attempts": max_entry_attempts,
                        "error": last_error_message,
                    },
                )
                time.sleep(ENTRY_RETRY_DELAY_SECONDS)
                continue

            logger.error("open_position failed", {"trade_id": trade_id, "error": last_error_message})
            try:
                move_state("FAILED")
            except Exception as state_error:
                logger.error(
                    "open_position state transition failed",
                    {"trade_id": trade_id, "error": to_error_message(state_error)},
                )
            return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {last_error_message}")

    if confirmed_submission is None or confirmed_entry_result is None:
        trade["execution"]["entry_error"] = last_error_message
        try:
            move_state("FAILED")
        except Exception as state_error:
            logger.error(
                "open_position state transition failed",
                {"trade_id": trade_id, "error": to_error_message(state_error)},
            )
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {last_error_message}")

    fallback_base_qty = (
        confirmed_submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
        if entry_side == "BUY_SOL_WITH_USDC"
        else confirmed_submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
    )
    traded_base_sol = (
        float(confirmed_entry_result["filled_base_sol"])
        if "filled_base_sol" in confirmed_entry_result
        else fallback_base_qty
    )
    if not isinstance(traded_base_sol, (int, float)) or traded_base_sol <= 0:
        quantity_error = (
            "filled quantity is 0: "
            f"filled_base_sol={confirmed_entry_result.get('filled_base_sol')}, "
            f"out_amount_atomic={confirmed_submission.out_amount_atomic}"
        )
        trade["execution"]["entry_error"] = quantity_error
        logger.error(
            "open_position failed: invalid filled quantity",
            {
                "trade_id": trade_id,
                "tx_signature": confirmed_submission.tx_signature,
                "filled_base_sol": confirmed_entry_result.get("filled_base_sol"),
                "out_amount_atomic": confirmed_submission.out_amount_atomic,
            },
        )
        move_state("FAILED")
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {quantity_error}")

    actual_quote_usdc = (
        float(confirmed_entry_result["spent_quote_usdc"])
        if "spent_quote_usdc" in confirmed_entry_result
        and isinstance(confirmed_entry_result["spent_quote_usdc"], (int, float))
        else effective_notional_usdc
    )
    fallback_entry_price = actual_quote_usdc / traded_base_sol
    resolved_entry_price = (
        float(confirmed_entry_result["avg_fill_price"])
        if "avg_fill_price" in confirmed_entry_result
        and isinstance(confirmed_entry_result["avg_fill_price"], (int, float))
        else fallback_entry_price
    )

    swing_stop = float(signal.stop_price)
    if direction == "LONG_ONLY":
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
    trade["position"]["quote_amount_usdc"] = round_to(actual_quote_usdc, 6)
    trade["position"]["entry_price"] = round_to(resolved_entry_price, 6)
    trade["position"]["stop_price"] = round_to(final_stop, 6)
    trade["position"]["take_profit_price"] = round_to(recalculated_take_profit, 6)
    trade["position"]["entry_time_iso"] = now_iso()
    trade["plan"]["entry_price"] = trade["position"]["entry_price"]
    trade["plan"]["stop_price"] = trade["position"]["stop_price"]
    trade["plan"]["take_profit_price"] = trade["position"]["take_profit_price"]
    trade["plan"]["summary"] = _build_plan_summary(
        direction,
        effective_notional_usdc,
        base_notional_usdc,
        volatility_regime,
        position_size_multiplier,
        trade["position"]["entry_price"],
        trade["position"]["stop_price"],
        trade["position"]["take_profit_price"],
    )

    move_state("CONFIRMED")
    logger.info(
        "trade risk levels aligned and persisted",
        {
            "trade_id": trade["trade_id"],
            "model_id": model_id,
            "direction": direction,
            "entry_price": trade["position"]["entry_price"],
            "stop_price": trade["position"]["stop_price"],
            "take_profit_price": trade["position"]["take_profit_price"],
            "volatility_regime": volatility_regime,
            "position_size_multiplier": position_size_multiplier,
            "notional_usdc": effective_notional_usdc,
        },
    )
    qty_summary = f"{trade['position']['quantity_sol']} SOL"
    attempt_note = f" after {attempt} attempts" if attempt > 1 else ""
    return OpenPositionResult(
        status="OPENED",
        trade_id=trade_id,
        summary=(
            f"OPENED: tx={confirmed_submission.tx_signature}, qty={qty_summary}, "
            f"direction={direction}{attempt_note}"
        ),
    )
