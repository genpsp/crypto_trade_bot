"""ABCs and value objects for the pluggable strategy component layer.

The split between layers is documented in docs/gmo_bot_logic_exploration_plan.md §2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from apps.dex_bot.domain.model.types import Direction, OhlcvBar


@dataclass(frozen=True)
class PositionContext:
    """Read-only view of an open position handed to ExitPolicy.update().

    Mirrors the subset of _OpenPosition fields an exit policy needs. Mutating the
    open position (e.g. moving the stop) is done via the returned ExitAction so
    that the engine remains the sole owner of position state.
    """

    direction: Direction
    entry_index: int
    entry_price: float
    stop_price: float
    take_profit_price: float
    atr_at_entry: float
    initial_stop_price: float
    initial_take_profit_price: float
    remaining_fraction: float = 1.0


@dataclass(frozen=True)
class HoldAction:
    """No-op: keep position as-is."""


@dataclass(frozen=True)
class BreakEvenAction:
    """Move stop to entry price (or a small offset)."""

    offset_pct: float = 0.0


@dataclass(frozen=True)
class TrailAction:
    """Update the stop to the given absolute price."""

    new_stop_price: float


@dataclass(frozen=True)
class PartialTpAction:
    """Close `fraction` of the remaining position at `price`."""

    fraction: float
    price: float
    reason: str = "PARTIAL_TAKE_PROFIT"


@dataclass(frozen=True)
class CloseAction:
    """Close the entire remaining position at `price` with the given reason."""

    price: float
    reason: str


ExitAction = HoldAction | BreakEvenAction | TrailAction | PartialTpAction | CloseAction


class ExitPolicy(ABC):
    """Per-bar exit decision against an open position.

    Called before the standard TP/SL touch check on every bar after entry. The
    returned ExitAction may move the stop (BE / trail), close a fraction (partial
    TP), or close the full position (time / volatility exit).
    """

    name: str = "exit"

    @abstractmethod
    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        ...


class RegimeGate(ABC):
    """Pre-entry filter. Returning False prevents the EntrySignal from running.

    `gate_state` is a per-backtest mutable dict the engine maintains; stateless
    gates ignore it, stateful gates (EquityCurveGate) read keys like
    `recent_r_multiples` from it.
    """

    name: str = "regime_gate"

    @abstractmethod
    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        ...

    def allow_for_direction(
        self,
        *,
        direction: Direction,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        """Post-direction filter, invoked AFTER the EntrySignal decides
        LONG/SHORT. Default implementation always allows; gates that need
        direction-aware logic override this. Direction-agnostic gates only
        use `allow()`.
        """

        return True

    def reject_reason(self) -> str:
        return f"REGIME_GATE_BLOCKED_BY_{self.name.upper()}"


class StopPolicy(ABC):
    """Convert a strategy ENTER decision into the initial stop price after the fill.

    The decision carries a candidate stop (`swing_low_stop`) computed pre-fill;
    the policy is allowed to tighten it against max-loss-pct or replace it
    entirely (e.g. fixed-ATR stop).
    """

    name: str = "stop"

    @abstractmethod
    def compute_initial_stop(
        self,
        *,
        decision: Any,
        direction: Direction,
        entry_price: float,
        max_loss_per_trade_pct: float,
        config: dict[str, Any],
    ) -> float | None:
        """Return the absolute stop price, or None to skip the trade."""


class SizingPolicy(ABC):
    name: str = "sizing"

    @abstractmethod
    def size_multiplier(
        self,
        *,
        decision: Any,
        config: dict[str, Any],
    ) -> float:
        ...


__all__ = [
    "BreakEvenAction",
    "CloseAction",
    "ExitAction",
    "ExitPolicy",
    "HoldAction",
    "PartialTpAction",
    "PositionContext",
    "RegimeGate",
    "SizingPolicy",
    "StopPolicy",
    "TrailAction",
]
