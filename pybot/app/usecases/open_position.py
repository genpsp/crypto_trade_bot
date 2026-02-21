from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pybot.app.ports.execution_port import ExecutionPort, SubmitSwapRequest
from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort
from pybot.app.ports.persistence_port import PersistencePort
from pybot.app.usecases.usecase_utils import now_iso, strip_none, to_error_message
from pybot.domain.model.trade_state import assert_trade_state_transition
from pybot.domain.model.types import BotConfig, EntrySignalDecision, TradeRecord, TradeState
from pybot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_take_profit_price,
    tighten_stop_for_long,
)
from pybot.domain.utils.math import round_to
from pybot.domain.utils.time import build_trade_id

USDC_ATOMIC_MULTIPLIER = 1_000_000
SOL_ATOMIC_MULTIPLIER = 1_000_000_000
TX_CONFIRM_TIMEOUT_MS = 75_000
TX_INFLIGHT_TTL_SECONDS = 180


@dataclass
class OpenPositionInput:
    config: BotConfig
    signal: EntrySignalDecision
    bar_close_time_iso: str


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


def open_position(dependencies: OpenPositionDependencies, input_data: OpenPositionInput) -> OpenPositionResult:
    execution = dependencies.execution
    lock = dependencies.lock
    logger = dependencies.logger
    persistence = dependencies.persistence
    config = input_data.config
    signal = input_data.signal
    bar_close_time_iso = input_data.bar_close_time_iso

    trade_id = build_trade_id(bar_close_time_iso)
    now = now_iso()
    configured_min_notional_usdc = float(config["execution"]["min_notional_usdc"])
    quote_balance_error: str | None = None
    try:
        available_quote_usdc = float(execution.get_available_quote_usdc(config["pair"]))
    except Exception as error:
        quote_balance_error = f"failed to fetch quote balance: {to_error_message(error)}"
        logger.error(
            "failed to fetch quote balance",
            {"pair": config["pair"], "error": to_error_message(error)},
        )
        available_quote_usdc = 0.0
    base_notional_usdc = round_to(available_quote_usdc, 6)
    volatility_regime = "NORMAL"
    position_size_multiplier = 1.0
    diagnostics = signal.diagnostics or {}
    raw_regime = diagnostics.get("volatility_regime")
    if isinstance(raw_regime, str) and raw_regime in ("NORMAL", "VOLATILE", "STORM"):
        volatility_regime = raw_regime
    raw_multiplier = diagnostics.get("position_size_multiplier")
    if isinstance(raw_multiplier, (int, float)) and raw_multiplier > 0:
        position_size_multiplier = float(raw_multiplier)

    effective_notional_usdc = round_to(base_notional_usdc * position_size_multiplier, 2)

    trade: TradeRecord = {
        "trade_id": trade_id,
        "bar_close_time_iso": bar_close_time_iso,
        "pair": config["pair"],
        "direction": config["direction"],
        "state": "CREATED",
        "config_version": config["meta"]["config_version"],
        "signal": {
            "summary": signal.summary,
            "bar_close_time_iso": bar_close_time_iso,
            "ema_fast": signal.ema_fast,
            "ema_slow": signal.ema_slow,
        },
        "plan": {
            "summary": (
                f"Buy SOL with {effective_notional_usdc} USDC "
                f"(base={base_notional_usdc}, regime={volatility_regime}, size_x={position_size_multiplier:.2f}), "
                f"stop={round_to(signal.stop_price, 4)}, tp={round_to(signal.take_profit_price, 4)}"
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

    if configured_min_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "min_notional_usdc must be > 0"
        move_state("FAILED")
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary="FAILED: invalid min_notional_usdc")

    if quote_balance_error:
        trade["execution"]["entry_error"] = quote_balance_error
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    if base_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "quote balance is 0"
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    if base_notional_usdc < configured_min_notional_usdc:
        trade["execution"]["entry_error"] = (
            f"insufficient quote balance: {base_notional_usdc} < min_notional_usdc "
            f"{configured_min_notional_usdc}"
        )
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    if effective_notional_usdc <= 0:
        trade["execution"]["entry_error"] = "effective_notional_usdc must be > 0"
        move_state("FAILED")
        return OpenPositionResult(
            status="FAILED",
            trade_id=trade_id,
            summary=f"FAILED: {trade['execution']['entry_error']}",
        )

    amount_atomic = int(round(effective_notional_usdc * USDC_ATOMIC_MULTIPLIER))

    try:
        submission = execution.submit_swap(
            SubmitSwapRequest(
                side="BUY_SOL_WITH_USDC",
                amount_atomic=amount_atomic,
                slippage_bps=config["execution"]["slippage_bps"],
                only_direct_routes=config["execution"]["only_direct_routes"],
            )
        )

        trade["execution"]["entry_tx_signature"] = submission.tx_signature
        if submission.order:
            trade["execution"]["order"] = submission.order
        entry_result = submission.result
        if entry_result is None:
            estimated_spent_quote_usdc = submission.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
            estimated_filled_base_sol = submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
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
        trade["execution"]["result"] = entry_result
        move_state("SUBMITTED")

        lock.set_inflight_tx(submission.tx_signature, TX_INFLIGHT_TTL_SECONDS)
        confirmation = execution.confirm_swap(submission.tx_signature, TX_CONFIRM_TIMEOUT_MS)
        lock.clear_inflight_tx(submission.tx_signature)

        if not confirmation.confirmed:
            trade["execution"]["entry_error"] = confirmation.error or "unknown confirmation error"
            move_state("FAILED")
            return OpenPositionResult(
                status="FAILED",
                trade_id=trade_id,
                summary=f"FAILED: entry tx not confirmed ({trade['execution']['entry_error']})",
            )

        fallback_received_sol = submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
        received_sol = (
            float(entry_result["filled_base_sol"])
            if "filled_base_sol" in entry_result
            else fallback_received_sol
        )
        if not isinstance(received_sol, (int, float)) or received_sol <= 0:
            quantity_error = (
                "filled quantity is 0: "
                f"filled_base_sol={entry_result.get('filled_base_sol')}, "
                f"out_amount_atomic={submission.out_amount_atomic}"
            )
            trade["execution"]["entry_error"] = quantity_error
            logger.error(
                "open_position failed: invalid filled quantity",
                {
                    "trade_id": trade_id,
                    "tx_signature": submission.tx_signature,
                    "filled_base_sol": entry_result.get("filled_base_sol"),
                    "out_amount_atomic": submission.out_amount_atomic,
                },
            )
            move_state("FAILED")
            return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {quantity_error}")

        fallback_entry_price = effective_notional_usdc / received_sol
        resolved_entry_price = (
            float(entry_result["avg_fill_price"])
            if "avg_fill_price" in entry_result
            else fallback_entry_price
        )
        swing_stop = float(signal.stop_price)
        pct_stop = calculate_max_loss_stop_price(resolved_entry_price, config["risk"]["max_loss_per_trade_pct"])
        final_stop = tighten_stop_for_long(
            resolved_entry_price, swing_stop, config["risk"]["max_loss_per_trade_pct"]
        )
        if final_stop >= resolved_entry_price:
            final_stop = pct_stop

        recalculated_take_profit = calculate_take_profit_price(
            resolved_entry_price, final_stop, config["exit"]["take_profit_r_multiple"]
        )

        trade["position"]["quantity_sol"] = round_to(received_sol, 9)
        trade["position"]["entry_price"] = round_to(resolved_entry_price, 6)
        trade["position"]["stop_price"] = round_to(final_stop, 6)
        trade["position"]["take_profit_price"] = round_to(recalculated_take_profit, 6)
        trade["position"]["entry_time_iso"] = now_iso()
        trade["plan"]["entry_price"] = trade["position"]["entry_price"]
        trade["plan"]["stop_price"] = trade["position"]["stop_price"]
        trade["plan"]["take_profit_price"] = trade["position"]["take_profit_price"]
        trade["plan"]["summary"] = (
            f"Buy SOL with {effective_notional_usdc} USDC "
            f"(base={base_notional_usdc}, regime={volatility_regime}, size_x={position_size_multiplier:.2f}), "
            f"entry={round_to(trade['position']['entry_price'], 4)}, "
            f"stop={round_to(trade['position']['stop_price'], 4)}, "
            f"tp={round_to(trade['position']['take_profit_price'], 4)}"
        )

        move_state("CONFIRMED")
        logger.info(
            "trade risk levels aligned and persisted",
            {
                "trade_id": trade["trade_id"],
                "entry_price": trade["position"]["entry_price"],
                "stop_price": trade["position"]["stop_price"],
                "take_profit_price": trade["position"]["take_profit_price"],
                "volatility_regime": volatility_regime,
                "position_size_multiplier": position_size_multiplier,
                "notional_usdc": effective_notional_usdc,
            },
        )
        return OpenPositionResult(
            status="OPENED",
            trade_id=trade_id,
            summary=f"OPENED: tx={submission.tx_signature}, qty={trade['position']['quantity_sol']} SOL",
        )
    except Exception as error:
        error_message = to_error_message(error)
        logger.error("open_position failed", {"trade_id": trade_id, "error": error_message})
        trade["execution"]["entry_error"] = error_message
        try:
            move_state("FAILED")
        except Exception as state_error:
            logger.error(
                "open_position state transition failed",
                {"trade_id": trade_id, "error": to_error_message(state_error)},
            )
        return OpenPositionResult(status="FAILED", trade_id=trade_id, summary=f"FAILED: {error_message}")
