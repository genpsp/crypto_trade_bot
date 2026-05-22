"""Concrete SizingPolicy implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.gmo_bot.domain.strategy.components.base import SizingPolicy


@dataclass(frozen=True)
class SizingState:
    """Aggregate state a SizingPolicy may inspect.

    The S1 layer only exposes diagnostics; equity-curve / loss-streak inputs
    will be added when Track E lands.
    """

    recent_close_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticsSizing(SizingPolicy):
    """Legacy behaviour: pull `position_size_multiplier` from diagnostics,
    defaulting to 1.0. Reproduces existing v0 numerics exactly."""

    name: str = "diagnostics"

    def size_multiplier(
        self,
        *,
        decision: Any,
        config: dict[str, Any],
    ) -> float:
        diagnostics = getattr(decision, "diagnostics", None)
        if diagnostics is None:
            return 1.0
        raw = diagnostics.get("position_size_multiplier")
        if isinstance(raw, (int, float)) and raw >= 0:
            return float(raw)
        return 1.0


__all__ = ["DiagnosticsSizing", "SizingState"]
