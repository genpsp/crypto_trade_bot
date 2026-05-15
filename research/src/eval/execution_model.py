from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import random
from pathlib import Path
from typing import Any, Protocol

from apps.dex_bot.domain.model.types import Direction, OhlcvBar

SLIPPAGE_BPS_DENOMINATOR = 10_000


@dataclass(frozen=True)
class EntryFill:
    price: float
    bar_index: int
    fill_time: datetime
    model_id: str
    slippage_bps: float


@dataclass(frozen=True)
class RejectedEntry:
    reason: str
    model_id: str


@dataclass(frozen=True)
class ExitFill:
    price: float
    reason: str
    model_id: str
    slippage_bps: float


class ExecutionModel(Protocol):
    model_id: str

    def simulate_entry_fill(
        self,
        *,
        decision: Any,
        direction: Direction,
        bars: list[OhlcvBar],
        index: int,
        slippage_bps: int,
        rng: random.Random,
    ) -> EntryFill | RejectedEntry: ...

    def simulate_stop_fill(
        self,
        *,
        position: Any,
        bar: OhlcvBar,
        slippage_bps: int,
        rng: random.Random,
    ) -> ExitFill: ...

    def simulate_tp_fill(
        self,
        *,
        position: Any,
        bar: OhlcvBar,
        slippage_bps: int,
        rng: random.Random,
    ) -> ExitFill: ...

    def simulate_same_bar_stop_and_tp(
        self,
        *,
        position: Any,
        bar: OhlcvBar,
        slippage_bps: int,
        rng: random.Random,
    ) -> ExitFill: ...


def _slippage_ratio(slippage_bps: float) -> float:
    return max(0.0, float(slippage_bps)) / SLIPPAGE_BPS_DENOMINATOR


def buy_fill_price(trigger_price: float, slippage_bps: float) -> float:
    """Return adverse BUY fill. Kept public for legacy analysis scripts."""

    return max(0.0, float(trigger_price)) * (1 + _slippage_ratio(slippage_bps))


def sell_fill_price(trigger_price: float, slippage_bps: float) -> float:
    """Return adverse SELL fill. Kept public for legacy analysis scripts."""

    return max(0.0, float(trigger_price) * (1 - _slippage_ratio(slippage_bps)))


def _next_bar_or_current(bars: list[OhlcvBar], index: int) -> tuple[int, OhlcvBar]:
    if index + 1 < len(bars):
        return index + 1, bars[index + 1]
    return index, bars[index]


class IdealExecutionModel:
    """Legacy deterministic model: decision price with fixed symmetric slippage."""

    model_id = "ideal_v1"

    def simulate_entry_fill(
        self,
        *,
        decision: Any,
        direction: Direction,
        bars: list[OhlcvBar],
        index: int,
        slippage_bps: int,
        rng: random.Random,
    ) -> EntryFill | RejectedEntry:
        price = (
            buy_fill_price(float(decision.entry_price), slippage_bps)
            if direction == "LONG"
            else sell_fill_price(float(decision.entry_price), slippage_bps)
        )
        return EntryFill(price=price, bar_index=index, fill_time=bars[index].close_time, model_id=self.model_id, slippage_bps=slippage_bps)

    def simulate_stop_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        if position.direction == "LONG":
            price = sell_fill_price(position.stop_price, slippage_bps)
        else:
            price = buy_fill_price(position.stop_price, slippage_bps)
        return ExitFill(price=price, reason="STOP_LOSS", model_id=self.model_id, slippage_bps=slippage_bps)

    def simulate_tp_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        if position.direction == "LONG":
            price = sell_fill_price(position.take_profit_price, slippage_bps)
        else:
            price = buy_fill_price(position.take_profit_price, slippage_bps)
        return ExitFill(price=price, reason="TAKE_PROFIT", model_id=self.model_id, slippage_bps=slippage_bps)

    def simulate_same_bar_stop_and_tp(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        fill = self.simulate_stop_fill(position=position, bar=bar, slippage_bps=slippage_bps, rng=rng)
        return ExitFill(price=fill.price, reason="STOP_LOSS_AND_TP_SAME_BAR", model_id=self.model_id, slippage_bps=fill.slippage_bps)


class PessimisticExecutionModel(IdealExecutionModel):
    """Deterministic adverse model for practical go/no-go screening."""

    model_id = "pessimistic_v1"

    def __init__(self, *, additional_slippage_bps: float = 3.0) -> None:
        self.additional_slippage_bps = max(0.0, float(additional_slippage_bps))

    def _bps(self, slippage_bps: int) -> float:
        return max(0, int(slippage_bps)) + self.additional_slippage_bps

    def simulate_entry_fill(
        self,
        *,
        decision: Any,
        direction: Direction,
        bars: list[OhlcvBar],
        index: int,
        slippage_bps: int,
        rng: random.Random,
    ) -> EntryFill | RejectedEntry:
        fill_index, fill_bar = _next_bar_or_current(bars, index)
        bps = self._bps(slippage_bps)
        price = buy_fill_price(fill_bar.open, bps) if direction == "LONG" else sell_fill_price(fill_bar.open, bps)
        return EntryFill(price=price, bar_index=fill_index, fill_time=fill_bar.open_time, model_id=self.model_id, slippage_bps=bps)

    def simulate_stop_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        bps = self._bps(slippage_bps)
        if position.direction == "LONG":
            trigger = min(float(bar.open), float(position.stop_price))
            price = sell_fill_price(trigger, bps)
        else:
            trigger = max(float(bar.open), float(position.stop_price))
            price = buy_fill_price(trigger, bps)
        return ExitFill(price=price, reason="STOP_LOSS", model_id=self.model_id, slippage_bps=bps)

    def simulate_tp_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        bps = self._bps(slippage_bps)
        if position.direction == "LONG":
            price = sell_fill_price(position.take_profit_price, bps)
        else:
            price = buy_fill_price(position.take_profit_price, bps)
        return ExitFill(price=price, reason="TAKE_PROFIT", model_id=self.model_id, slippage_bps=bps)


class StochasticExecutionModel(PessimisticExecutionModel):
    """Profile-driven stochastic model with reject, latency, and slippage draws."""

    model_id = "stochastic_v1"

    def __init__(self, *, profile: dict[str, Any] | None = None, additional_slippage_bps: float = 0.0) -> None:
        super().__init__(additional_slippage_bps=additional_slippage_bps)
        self.profile = profile or {}

    def _profile_bucket(self, direction: Direction | None = None) -> dict[str, Any]:
        by_direction = self.profile.get("by_direction")
        if isinstance(by_direction, dict) and direction in by_direction and isinstance(by_direction[direction], dict):
            return by_direction[direction]
        return self.profile

    def _reject_probability(self, direction: Direction | None = None) -> float:
        bucket = self._profile_bucket(direction)
        raw = bucket.get("p_reject", bucket.get("reject_probability", bucket.get("reject_rate", self.profile.get("p_reject", 0.0))))
        try:
            return min(1.0, max(0.0, float(raw)))
        except Exception:
            return 0.0

    def _draw_slippage_bps(self, *, direction: Direction | None, base_bps: int, rng: random.Random) -> float:
        bucket = self._profile_bucket(direction)
        params = bucket.get("slippage_bps") if isinstance(bucket.get("slippage_bps"), dict) else bucket
        if not isinstance(params, dict):
            params = {}
        mean_bps = float(params.get("mean", params.get("mean_bps", base_bps)))
        std_bps = max(0.0, float(params.get("std", params.get("std_bps", 0.0))))
        distribution = str(params.get("distribution", "normal")).lower()
        if distribution == "lognormal":
            sigma = std_bps / max(1.0, mean_bps)
            draw = rng.lognormvariate(math.log(max(0.01, mean_bps)), sigma)
        else:
            draw = rng.gauss(mean_bps, std_bps)
        return max(0.0, draw + self.additional_slippage_bps)

    def _latency_weight(self, bars: list[OhlcvBar], index: int) -> float:
        raw = self.profile.get("latency_seconds", self.profile.get("mean_latency_seconds", 0.0))
        try:
            latency_seconds = max(0.0, float(raw))
        except Exception:
            return 0.0
        if index + 1 >= len(bars) or latency_seconds <= 0:
            return 0.0
        bar_seconds = max(1.0, (bars[index + 1].open_time - bars[index].open_time).total_seconds())
        return min(1.0, latency_seconds / bar_seconds)

    def simulate_entry_fill(
        self,
        *,
        decision: Any,
        direction: Direction,
        bars: list[OhlcvBar],
        index: int,
        slippage_bps: int,
        rng: random.Random,
    ) -> EntryFill | RejectedEntry:
        if rng.random() < self._reject_probability(direction):
            return RejectedEntry(reason="ENTRY_REJECTED_BY_EXECUTION_MODEL", model_id=self.model_id)
        fill_index, next_bar = _next_bar_or_current(bars, index)
        latency_weight = self._latency_weight(bars, index)
        base_price = (1 - latency_weight) * float(decision.entry_price) + latency_weight * float(next_bar.open)
        if fill_index != index and latency_weight >= 1.0:
            base_price = float(next_bar.open)
        bps = self._draw_slippage_bps(direction=direction, base_bps=slippage_bps, rng=rng)
        price = buy_fill_price(base_price, bps) if direction == "LONG" else sell_fill_price(base_price, bps)
        fill_time = next_bar.open_time if fill_index != index and latency_weight > 0 else bars[index].close_time
        return EntryFill(price=price, bar_index=fill_index if latency_weight >= 1.0 else index, fill_time=fill_time, model_id=self.model_id, slippage_bps=bps)

    def simulate_stop_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        bps = self._draw_slippage_bps(direction=position.direction, base_bps=slippage_bps, rng=rng)
        if position.direction == "LONG":
            trigger = min(float(bar.open), float(position.stop_price))
            price = sell_fill_price(trigger, bps)
        else:
            trigger = max(float(bar.open), float(position.stop_price))
            price = buy_fill_price(trigger, bps)
        return ExitFill(price=price, reason="STOP_LOSS", model_id=self.model_id, slippage_bps=bps)

    def simulate_tp_fill(self, *, position: Any, bar: OhlcvBar, slippage_bps: int, rng: random.Random) -> ExitFill:
        bps = self._draw_slippage_bps(direction=position.direction, base_bps=slippage_bps, rng=rng)
        if position.direction == "LONG":
            price = sell_fill_price(position.take_profit_price, bps)
        else:
            price = buy_fill_price(position.take_profit_price, bps)
        return ExitFill(price=price, reason="TAKE_PROFIT", model_id=self.model_id, slippage_bps=bps)


def _load_profile(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    path = Path(str(raw))
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    return {}


def build_execution_model(execution: dict[str, Any]) -> ExecutionModel:
    raw_model_id = (
        execution.get("model_id")
        or execution.get("execution_model_id")
        or execution.get("execution_model")
        or "ideal_v1"
    )
    if isinstance(raw_model_id, dict):
        raw_model_id = raw_model_id.get("id", "ideal_v1")
    model_id = str(raw_model_id).lower()
    if model_id in {"ideal", "ideal_v1", "legacy"}:
        return IdealExecutionModel()
    if model_id in {"pessimistic", "pessimistic_v1"}:
        return PessimisticExecutionModel(additional_slippage_bps=float(execution.get("additional_slippage_bps", 3.0)))
    if model_id in {"stochastic", "stochastic_v1"}:
        profile = _load_profile(execution.get("profile") or execution.get("execution_profile") or execution.get("profile_path"))
        inline_profile = execution.get("profile_data")
        if isinstance(inline_profile, dict):
            profile = {**profile, **inline_profile}
        # Direct shorthand keys are accepted for smoke tests and hand-written specs.
        for key in ("p_reject", "reject_probability", "reject_rate", "latency_seconds", "mean_latency_seconds", "slippage_bps"):
            if key in execution and key not in profile:
                profile[key] = execution[key]
        return StochasticExecutionModel(profile=profile, additional_slippage_bps=float(execution.get("additional_slippage_bps", 0.0)))
    raise ValueError(f"unsupported execution model_id: {raw_model_id}")
