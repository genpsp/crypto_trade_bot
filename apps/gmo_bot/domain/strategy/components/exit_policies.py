"""Concrete ExitPolicy implementations covering Track A of the logic exploration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.dex_bot.domain.model.types import OhlcvBar
from apps.gmo_bot.domain.strategy.components.base import (
    BreakEvenAction,
    CloseAction,
    ExitAction,
    ExitPolicy,
    HoldAction,
    PartialTpAction,
    PositionContext,
    TrailAction,
)


@dataclass(frozen=True)
class FixedRExit(ExitPolicy):
    """Legacy behaviour: stop / TP stay where they were set at entry.

    The engine's standard touch-check handles the actual exit; this policy never
    issues an action. Keeping it explicit lets us swap it in by composition.
    """

    name: str = "fixed_r"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        return HoldAction()


@dataclass(frozen=True)
class BreakEvenExit(ExitPolicy):
    """A1: once price has travelled `trigger_r * initial_risk` in favour, move
    the stop to the entry price (plus an optional small offset)."""

    trigger_r: float = 1.0
    offset_pct: float = 0.0
    name: str = "break_even"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        initial_risk = abs(position.entry_price - position.initial_stop_price)
        if initial_risk <= 0:
            return HoldAction()
        trigger_distance = initial_risk * self.trigger_r
        if position.direction == "LONG":
            target_price = position.entry_price + trigger_distance
            if bar.high < target_price:
                return HoldAction()
            new_stop = position.entry_price * (1.0 + self.offset_pct / 100.0)
            if new_stop <= position.stop_price:
                return HoldAction()
        else:
            target_price = position.entry_price - trigger_distance
            if bar.low > target_price:
                return HoldAction()
            new_stop = position.entry_price * (1.0 - self.offset_pct / 100.0)
            if new_stop >= position.stop_price:
                return HoldAction()
        return BreakEvenAction(offset_pct=self.offset_pct)


@dataclass(frozen=True)
class TimeExit(ExitPolicy):
    """A4: close the position at market if it has not exited within
    `max_holding_bars` bars after entry. Optional break-even cap to refuse losing
    closes (set `prefer_breakeven=True` to clamp the exit price to entry)."""

    max_holding_bars: int = 60
    prefer_breakeven: bool = False
    name: str = "time_exit"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        holding = bar_index - position.entry_index
        if holding < self.max_holding_bars:
            return HoldAction()
        price = float(bar.close)
        if self.prefer_breakeven:
            if position.direction == "LONG":
                price = max(price, position.entry_price)
            else:
                price = min(price, position.entry_price)
        return CloseAction(price=price, reason="TIME_EXIT")


@dataclass(frozen=True)
class PartialTpExit(ExitPolicy):
    """A2: close `partial_fraction` of the position once price reaches
    `partial_r * initial_risk`. Subsequent bars hold the runner against the
    original TP (or the trailing stop installed by the engine)."""

    partial_r: float = 1.0
    partial_fraction: float = 0.5
    name: str = "partial_tp"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        if position.remaining_fraction <= self.partial_fraction:
            return HoldAction()
        initial_risk = abs(position.entry_price - position.initial_stop_price)
        if initial_risk <= 0:
            return HoldAction()
        if position.direction == "LONG":
            target = position.entry_price + initial_risk * self.partial_r
            if bar.high < target:
                return HoldAction()
            price = target
        else:
            target = position.entry_price - initial_risk * self.partial_r
            if bar.low > target:
                return HoldAction()
            price = target
        return PartialTpAction(fraction=self.partial_fraction, price=price)


@dataclass(frozen=True)
class ChandelierTrailExit(ExitPolicy):
    """A3: trail the stop at `highest_high - atr_multiple * ATR` for LONG
    (mirror for SHORT). ATR is taken from `atr_at_entry`; if a later iteration
    wants live ATR it can be added without changing the contract."""

    atr_multiple: float = 2.5
    name: str = "chandelier"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        if position.atr_at_entry <= 0:
            return HoldAction()
        trail_distance = self.atr_multiple * position.atr_at_entry
        if position.direction == "LONG":
            candidate = float(bar.high) - trail_distance
            if candidate <= position.stop_price:
                return HoldAction()
            return TrailAction(new_stop_price=candidate)
        candidate = float(bar.low) + trail_distance
        if candidate >= position.stop_price:
            return HoldAction()
        return TrailAction(new_stop_price=candidate)


@dataclass(frozen=True)
class CompositeExit(ExitPolicy):
    """Run several ExitPolicies in order; the first non-Hold action wins.

    Allows mixing e.g. PartialTpExit + BreakEvenExit + TimeExit on a single bar.
    """

    policies: tuple[ExitPolicy, ...] = ()
    name: str = "composite"

    def update(
        self,
        *,
        position: PositionContext,
        bar: OhlcvBar,
        bar_index: int,
        config: dict[str, Any],
    ) -> ExitAction:
        for policy in self.policies:
            action = policy.update(position=position, bar=bar, bar_index=bar_index, config=config)
            if not isinstance(action, HoldAction):
                return action
        return HoldAction()


__all__ = [
    "BreakEvenExit",
    "ChandelierTrailExit",
    "CompositeExit",
    "FixedRExit",
    "PartialTpExit",
    "TimeExit",
]
