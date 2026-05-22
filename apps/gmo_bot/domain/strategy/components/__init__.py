"""Pluggable strategy components for the gmo_bot.

Each strategy is composed of five layers that can be swapped independently:

* RegimeGate   — pre-entry filter that may forbid trading at all
* EntrySignal  — produces a StrategyDecision (ENTER or NO_SIGNAL)
* StopPolicy   — turns a decision into an initial stop price after the fill
* ExitPolicy   — per-bar action against an open position (BE / partial / trail / time)
* SizingPolicy — translates diagnostics + state into a notional size multiplier

See [docs/gmo_bot_logic_exploration_plan.md](../../../../../docs/gmo_bot_logic_exploration_plan.md)
for the broader exploration plan that these layers were designed to support.
"""

from apps.gmo_bot.domain.strategy.components.base import (
    BreakEvenAction,
    CloseAction,
    ExitAction,
    ExitPolicy,
    HoldAction,
    PartialTpAction,
    PositionContext,
    RegimeGate,
    SizingPolicy,
    StopPolicy,
    TrailAction,
)
from apps.gmo_bot.domain.strategy.components.exit_policies import (
    BreakEvenExit,
    ChandelierTrailExit,
    CompositeExit,
    FixedRExit,
    PartialTpExit,
    TimeExit,
)
from apps.gmo_bot.domain.strategy.components.regime_gates import NullRegimeGate
from apps.gmo_bot.domain.strategy.components.sizing_policies import (
    DiagnosticsSizing,
    SizingState,
)
from apps.gmo_bot.domain.strategy.components.stop_policies import LegacyTightenedStop
from apps.gmo_bot.domain.strategy.components.bundle import StrategyBundle, resolve_strategy_bundle

__all__ = [
    "BreakEvenAction",
    "BreakEvenExit",
    "ChandelierTrailExit",
    "CloseAction",
    "CompositeExit",
    "DiagnosticsSizing",
    "ExitAction",
    "ExitPolicy",
    "FixedRExit",
    "HoldAction",
    "LegacyTightenedStop",
    "NullRegimeGate",
    "PartialTpAction",
    "PartialTpExit",
    "PositionContext",
    "RegimeGate",
    "SizingPolicy",
    "SizingState",
    "StopPolicy",
    "StrategyBundle",
    "TimeExit",
    "TrailAction",
    "resolve_strategy_bundle",
]
