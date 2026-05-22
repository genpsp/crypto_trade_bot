"""Resolve `strategy.components` config dicts into concrete component instances.

The engine calls `resolve_strategy_bundle(strategy_config)` once per backtest
and reuses the returned bundle across bars.

Config schema (all keys optional; missing keys yield the legacy default):

    strategy:
      name: ema_trend_pullback_15m_v2
      components:
        regime_gate:
          type: null_gate            # default; equivalent to legacy "no gate"
        stop_policy:
          type: legacy_tightened
        exit_policy:
          type: fixed_r              # default; legacy fixed TP / SL
          # Alternatives, with their parameters:
          # type: break_even
          # trigger_r: 1.0
          # offset_pct: 0.0
          # ---
          # type: time_exit
          # max_holding_bars: 60
          # prefer_breakeven: false
          # ---
          # type: partial_tp
          # partial_r: 1.0
          # partial_fraction: 0.5
          # ---
          # type: chandelier
          # atr_multiple: 2.5
          # ---
          # type: composite
          # policies:
          #   - { type: partial_tp, partial_r: 1.0, partial_fraction: 0.5 }
          #   - { type: break_even, trigger_r: 1.0 }
          #   - { type: time_exit, max_holding_bars: 120, prefer_breakeven: true }
        sizing_policy:
          type: diagnostics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.gmo_bot.domain.strategy.components.base import (
    ExitPolicy,
    RegimeGate,
    SizingPolicy,
    StopPolicy,
)
from apps.gmo_bot.domain.strategy.components.exit_policies import (
    BreakEvenExit,
    ChandelierTrailExit,
    CompositeExit,
    FixedRExit,
    PartialTpExit,
    TimeExit,
)
from apps.gmo_bot.domain.strategy.components.regime_gates import (
    ADXGate,
    ATRPctRangeGate,
    BtcMomentumGate,
    CompositeRegimeGate,
    DirectionalSessionGate,
    DonchianWidthGate,
    EquityCurveGate,
    NullRegimeGate,
    SessionGate,
    VolumeConfirmedGate,
)
from apps.gmo_bot.domain.strategy.components.sizing_policies import DiagnosticsSizing
from apps.gmo_bot.domain.strategy.components.stop_policies import LegacyTightenedStop


@dataclass(frozen=True)
class StrategyBundle:
    regime_gate: RegimeGate
    stop_policy: StopPolicy
    exit_policy: ExitPolicy
    sizing_policy: SizingPolicy


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _build_exit_policy(spec: dict[str, Any]) -> ExitPolicy:
    type_id = str(spec.get("type", "fixed_r")).lower()
    if type_id == "fixed_r":
        return FixedRExit()
    if type_id == "break_even":
        return BreakEvenExit(
            trigger_r=float(spec.get("trigger_r", 1.0)),
            offset_pct=float(spec.get("offset_pct", 0.0)),
        )
    if type_id == "time_exit":
        return TimeExit(
            max_holding_bars=int(spec.get("max_holding_bars", 60)),
            prefer_breakeven=bool(spec.get("prefer_breakeven", False)),
        )
    if type_id == "partial_tp":
        return PartialTpExit(
            partial_r=float(spec.get("partial_r", 1.0)),
            partial_fraction=float(spec.get("partial_fraction", 0.5)),
        )
    if type_id == "chandelier":
        return ChandelierTrailExit(atr_multiple=float(spec.get("atr_multiple", 2.5)))
    if type_id == "composite":
        sub_specs = spec.get("policies") or []
        if not isinstance(sub_specs, list):
            raise ValueError("composite exit_policy.policies must be a list")
        policies = tuple(_build_exit_policy(_coerce_dict(item)) for item in sub_specs)
        return CompositeExit(policies=policies)
    raise ValueError(f"unsupported exit_policy.type: {type_id}")


def _build_regime_gate(spec: dict[str, Any]) -> RegimeGate:
    type_id = str(spec.get("type", "null_gate")).lower()
    if type_id == "null_gate":
        return NullRegimeGate()
    if type_id == "adx":
        return ADXGate(
            period=int(spec.get("period", 14)),
            min_adx=float(spec.get("min_adx", 20.0)),
            max_adx=float(spec.get("max_adx", 60.0)),
        )
    if type_id == "donchian_width":
        return DonchianWidthGate(
            donchian_period=int(spec.get("donchian_period", 24)),
            atr_period=int(spec.get("atr_period", 14)),
            width_atr_threshold=float(spec.get("width_atr_threshold", 3.0)),
        )
    if type_id == "equity_curve":
        return EquityCurveGate(
            lookback_trades=int(spec.get("lookback_trades", 20)),
            min_trades=int(spec.get("min_trades", 10)),
        )
    if type_id == "session":
        hours = spec.get("allowed_utc_hours", ()) or ()
        return SessionGate(allowed_utc_hours=tuple(int(h) for h in hours))
    if type_id == "directional_session":
        long_hours = spec.get("long_allowed_utc_hours", ()) or ()
        short_hours = spec.get("short_allowed_utc_hours", ()) or ()
        return DirectionalSessionGate(
            long_allowed_utc_hours=tuple(int(h) for h in long_hours),
            short_allowed_utc_hours=tuple(int(h) for h in short_hours),
        )
    if type_id == "volume_confirmed":
        return VolumeConfirmedGate(
            period=int(spec.get("period", 20)),
            volume_multiplier=float(spec.get("volume_multiplier", 1.5)),
        )
    if type_id == "atr_pct_range":
        return ATRPctRangeGate(
            period=int(spec.get("period", 14)),
            min_atr_pct=float(spec.get("min_atr_pct", 0.0)),
            max_atr_pct=float(spec.get("max_atr_pct", 100.0)),
        )
    if type_id == "btc_momentum":
        return BtcMomentumGate(
            bars_path=str(
                spec.get("bars_path", "research/data/raw/btcjpy_15m_1y.csv")
            ),
            lookback_bars=int(spec.get("lookback_bars", 4)),
            min_abs_return_pct=float(spec.get("min_abs_return_pct", 0.3)),
        )
    if type_id == "composite":
        sub_specs = spec.get("gates") or []
        if not isinstance(sub_specs, list):
            raise ValueError("composite regime_gate.gates must be a list")
        gates = tuple(_build_regime_gate(_coerce_dict(item)) for item in sub_specs)
        return CompositeRegimeGate(gates=gates)
    raise ValueError(f"unsupported regime_gate.type: {type_id}")


def _build_stop_policy(spec: dict[str, Any]) -> StopPolicy:
    type_id = str(spec.get("type", "legacy_tightened")).lower()
    if type_id == "legacy_tightened":
        return LegacyTightenedStop()
    raise ValueError(f"unsupported stop_policy.type: {type_id}")


def _build_sizing_policy(spec: dict[str, Any]) -> SizingPolicy:
    type_id = str(spec.get("type", "diagnostics")).lower()
    if type_id == "diagnostics":
        return DiagnosticsSizing()
    raise ValueError(f"unsupported sizing_policy.type: {type_id}")


def resolve_strategy_bundle(strategy_config: dict[str, Any]) -> StrategyBundle:
    """Build the StrategyBundle from a strategy config dict.

    If `strategy_config["components"]` is absent the bundle returned reproduces
    the v0 behaviour byte-for-byte. This is what the v2 reproduction test
    depends on.
    """
    components = _coerce_dict(strategy_config.get("components"))
    return StrategyBundle(
        regime_gate=_build_regime_gate(_coerce_dict(components.get("regime_gate"))),
        stop_policy=_build_stop_policy(_coerce_dict(components.get("stop_policy"))),
        exit_policy=_build_exit_policy(_coerce_dict(components.get("exit_policy"))),
        sizing_policy=_build_sizing_policy(_coerce_dict(components.get("sizing_policy"))),
    )


__all__ = ["StrategyBundle", "resolve_strategy_bundle"]
