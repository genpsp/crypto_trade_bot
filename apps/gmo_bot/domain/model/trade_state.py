from __future__ import annotations

from .types import TradeState

TRADE_STATE_VALUES: tuple[TradeState, ...] = (
    "CREATED",
    "SUBMITTED",
    "CONFIRMED",
    "CLOSED",
    "FAILED",
    "CANCELED",
)

ALLOWED_TRANSITIONS: dict[TradeState, tuple[TradeState, ...]] = {
    "CREATED": ("SUBMITTED", "FAILED", "CANCELED"),
    "SUBMITTED": ("CONFIRMED", "FAILED", "CANCELED"),
    "CONFIRMED": ("SUBMITTED", "CLOSED", "FAILED", "CANCELED"),
    "CLOSED": (),
    "FAILED": (),
    "CANCELED": (),
}


def can_transition_trade_state(from_state: TradeState, to_state: TradeState) -> bool:
    return to_state in ALLOWED_TRANSITIONS[from_state]


def assert_trade_state_transition(from_state: TradeState, to_state: TradeState) -> None:
    if not can_transition_trade_state(from_state, to_state):
        raise ValueError(f"Invalid trade state transition: {from_state} -> {to_state}")

