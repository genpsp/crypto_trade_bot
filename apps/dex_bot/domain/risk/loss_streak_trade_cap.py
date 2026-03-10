from __future__ import annotations

from typing import Sequence

LOSS_STREAK_DYNAMIC_CAP_STRATEGY_NAMES = frozenset({"ema_trend_pullback_15m_v0"})
LOSS_STREAK_LOOKBACK_CLOSED_TRADES = 20
LOSS_STREAK_CAP_LEVEL_1_THRESHOLD = 2
LOSS_STREAK_CAP_LEVEL_2_THRESHOLD = 3
LOSS_STREAK_CAP_LEVEL_1_MAX_TRADES = 2
LOSS_STREAK_CAP_LEVEL_2_MAX_TRADES = 1


def is_loss_streak_dynamic_cap_enabled(strategy_name: str) -> bool:
    return strategy_name in LOSS_STREAK_DYNAMIC_CAP_STRATEGY_NAMES


def count_consecutive_stop_losses(recent_close_reasons: Sequence[str | None]) -> int:
    streak = 0
    for close_reason in recent_close_reasons:
        if close_reason == "STOP_LOSS":
            streak += 1
            continue
        if close_reason == "TAKE_PROFIT":
            break
        # Unknown/manual/system closes should not bias the streak.
        break
    return streak


def resolve_effective_max_trades_per_day_for_strategy(
    *,
    strategy_name: str,
    base_max_trades_per_day: int,
    recent_close_reasons: Sequence[str | None],
) -> tuple[int, int, str]:
    if not is_loss_streak_dynamic_cap_enabled(strategy_name):
        return base_max_trades_per_day, 0, "DISABLED"

    consecutive_loss_streak = count_consecutive_stop_losses(recent_close_reasons)

    effective_max_trades_per_day = base_max_trades_per_day
    dynamic_cap_reason = "BASE"
    if consecutive_loss_streak >= LOSS_STREAK_CAP_LEVEL_2_THRESHOLD:
        effective_max_trades_per_day = min(base_max_trades_per_day, LOSS_STREAK_CAP_LEVEL_2_MAX_TRADES)
        dynamic_cap_reason = "LOSS_STREAK_GE_3"
    elif consecutive_loss_streak >= LOSS_STREAK_CAP_LEVEL_1_THRESHOLD:
        effective_max_trades_per_day = min(base_max_trades_per_day, LOSS_STREAK_CAP_LEVEL_1_MAX_TRADES)
        dynamic_cap_reason = "LOSS_STREAK_GE_2"

    return effective_max_trades_per_day, consecutive_loss_streak, dynamic_cap_reason
