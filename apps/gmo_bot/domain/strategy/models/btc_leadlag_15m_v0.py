"""BTC lead-lag entry signal (Track ④).

仮説 BTC の動きが SOL/JPY に先行する
post-mortem で BTC 4bar リターンが明確に動く端で SOL の WR が高い ことを確認済み
（btc_ret_4bar U字 spread 15pp）これを gate ではなく entry 信号へ拡張する

- BTC の lookback_bars リターン符号で SOL entry 方向を決める
  ret >= +min_abs_return_pct → LONG / ret <= -min_abs_return_pct → SHORT
- entry は SOL 現在 close stop は ATR ベース TP は exit.take_profit_r_multiple の R 倍

BTC bars は open_time(UTC) で SOL に整合 research 専用
（本番は market-data port 経由にする）
"""

from __future__ import annotations

import bisect
from datetime import UTC

from apps.dex_bot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    ModelDirection,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from apps.dex_bot.domain.strategy.shared.decision_builders import (
    build_entry_signal,
    build_no_signal,
)
from apps.gmo_bot.domain.strategy.components.regime_gates import _load_external_bars
from apps.gmo_bot.domain.strategy.models.mean_reversion_15m_v0 import _atr_at


STRATEGY_NAME = "btc_leadlag_15m_v0"


def _btc_return_pct(
    bars: list[OhlcvBar], index_bar: OhlcvBar, bars_path: str, lookback_bars: int
) -> float | None:
    """SOL の対象 bar 時刻に整合する BTC の lookback リターン(%)を返す

    整合不能 / warmup 未達なら None
    """
    try:
        btc_bars, btc_index = _load_external_bars(bars_path)
    except Exception:
        return None
    target = index_bar.open_time.astimezone(UTC)
    sorted_keys = sorted(btc_index.keys())
    pos = bisect.bisect_right(sorted_keys, target) - 1
    if pos < 0:
        return None
    btc_idx = btc_index[sorted_keys[pos]]
    if btc_idx < lookback_bars:
        return None
    prior_close = btc_bars[btc_idx - lookback_bars].close
    current_close = btc_bars[btc_idx].close
    if prior_close <= 0:
        return None
    return (current_close - prior_close) / prior_close * 100


def evaluate_btc_leadlag_15m_v0(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    bars_path = str(strategy.get("btc_bars_path", "research/data/raw/btcjpy_15m_1y.csv"))
    lookback_bars = int(strategy.get("btc_lookback_bars", 4))
    min_abs_return_pct = float(strategy.get("btc_min_abs_return_pct", 0.5))
    atr_period = int(strategy.get("atr_period", 14))
    atr_stop_multiplier = float(strategy.get("atr_stop_multiplier", 1.5))

    if len(bars) < atr_period + 1:
        return build_no_signal(
            summary=f"NO_SIGNAL: warmup not reached (bars={len(bars)})",
            reason="EMA_NOT_STABLE",
            diagnostics={"bars_count": len(bars)},
        )

    current = bars[-1]
    btc_ret = _btc_return_pct(bars, current, bars_path, lookback_bars)
    if btc_ret is None:
        return build_no_signal(
            summary="NO_SIGNAL: BTC bar unavailable for alignment",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={"btc_ret_pct": None},
        )

    if btc_ret >= min_abs_return_pct:
        entry_direction = "LONG"
    elif btc_ret <= -min_abs_return_pct:
        entry_direction = "SHORT"
    else:
        return build_no_signal(
            summary=f"NO_SIGNAL: BTC sideways ({btc_ret:+.2f}% < {min_abs_return_pct})",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={"btc_ret_pct": btc_ret},
        )

    if entry_direction == "LONG" and direction not in ("LONG", "BOTH"):
        return build_no_signal(
            summary="NO_SIGNAL: long signal but direction forbids LONG",
            reason="MODEL_DIRECTION_SHORT_ONLY",
            diagnostics={"entry_direction": "LONG"},
        )
    if entry_direction == "SHORT" and direction not in ("SHORT", "BOTH"):
        return build_no_signal(
            summary="NO_SIGNAL: short signal but direction forbids SHORT",
            reason="MODEL_DIRECTION_LONG_ONLY",
            diagnostics={"entry_direction": "SHORT"},
        )

    atr_value = _atr_at(bars, atr_period)
    if atr_value <= 0:
        return build_no_signal(
            summary="NO_SIGNAL: ATR not stable",
            reason="EMA_NOT_STABLE",
            diagnostics={"atr": atr_value},
        )

    entry_price = current.close
    if entry_direction == "LONG":
        stop_price = entry_price - atr_stop_multiplier * atr_value
    else:
        stop_price = entry_price + atr_stop_multiplier * atr_value

    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        return build_no_signal(
            summary="NO_SIGNAL: degenerate risk",
            reason="INVALID_RISK_AFTER_FILL",
            diagnostics={"stop_candidate": stop_price, "entry": entry_price},
        )

    take_profit_r_multiple = float(exit.get("take_profit_r_multiple", 2.0))
    take_profit_price = (
        entry_price + take_profit_r_multiple * risk_per_unit
        if entry_direction == "LONG"
        else entry_price - take_profit_r_multiple * risk_per_unit
    )

    return build_entry_signal(
        summary=f"ENTER: btc lead-lag {entry_direction} (btc {btc_ret:+.2f}%)",
        ema_fast=entry_price,
        ema_slow=entry_price,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        diagnostics={
            "atr": atr_value,
            "position_size_multiplier": 1.0,
            "entry_direction": entry_direction,
            "btc_ret_pct": btc_ret,
            "btc_lookback_bars": lookback_bars,
        },
    )


__all__ = ["STRATEGY_NAME", "evaluate_btc_leadlag_15m_v0"]
