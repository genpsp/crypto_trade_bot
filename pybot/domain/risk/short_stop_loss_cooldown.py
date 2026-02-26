from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

from pybot.domain.model.types import TradeRecord

SHORT_STOP_LOSS_COOLDOWN_STRATEGY_NAMES = frozenset({"ema_trend_pullback_15m_v0"})
SHORT_STOP_LOSS_COOLDOWN_BARS = 8
SHORT_STOP_LOSS_COOLDOWN_REASON = "SHORT_COOLDOWN_AFTER_STOP_LOSS_ACTIVE"


def is_short_stop_loss_cooldown_enabled(strategy_name: str) -> bool:
    return strategy_name in SHORT_STOP_LOSS_COOLDOWN_STRATEGY_NAMES


def resolve_short_stop_loss_cooldown_state(
    *,
    strategy_name: str,
    recent_closed_trades: Sequence[TradeRecord],
    current_bar_close_time: datetime,
    bar_duration_seconds: int,
) -> tuple[bool, int | None, int | None]:
    if not is_short_stop_loss_cooldown_enabled(strategy_name):
        return False, None, None
    if bar_duration_seconds <= 0:
        return False, None, None

    latest_short_trade = _find_latest_short_closed_trade(recent_closed_trades)
    if latest_short_trade is None:
        return False, None, None
    if latest_short_trade.get("close_reason") != "STOP_LOSS":
        return False, None, None

    close_time = _extract_trade_close_time(latest_short_trade)
    if close_time is None:
        return False, None, None

    elapsed_seconds = int((current_bar_close_time.astimezone(UTC) - close_time).total_seconds())
    if elapsed_seconds < 0:
        return False, None, None

    elapsed_bars = elapsed_seconds // bar_duration_seconds
    remaining_bars = SHORT_STOP_LOSS_COOLDOWN_BARS - elapsed_bars
    if remaining_bars <= 0:
        return False, elapsed_bars, 0
    return True, elapsed_bars, remaining_bars


def _find_latest_short_closed_trade(recent_closed_trades: Sequence[TradeRecord]) -> TradeRecord | None:
    for trade in recent_closed_trades:
        if trade.get("direction") == "SHORT":
            return trade
    return None


def _extract_trade_close_time(trade: TradeRecord) -> datetime | None:
    position = trade.get("position")
    if isinstance(position, dict):
        value = _parse_iso_datetime(position.get("exit_time_iso"))
        if value is not None:
            return value

    value = _parse_iso_datetime(trade.get("updated_at"))
    if value is not None:
        return value
    return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
