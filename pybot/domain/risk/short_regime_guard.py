from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

from pybot.domain.model.types import TradeRecord

SHORT_REGIME_GUARD_STRATEGY_NAMES = frozenset({"ema_trend_pullback_15m_v0"})
SHORT_REGIME_GUARD_REASON = "SHORT_REGIME_GUARD_ACTIVE"

SHORT_REGIME_GUARD_LOOKBACK_SHORT_TRADES = 8
SHORT_REGIME_GUARD_MIN_SHORT_TRADES = 6
SHORT_REGIME_GUARD_MIN_CONSECUTIVE_STOP_LOSSES = 4
SHORT_REGIME_GUARD_MAX_WIN_RATE_PCT = 20.0
SHORT_REGIME_GUARD_BLOCK_BARS = 96


def is_short_regime_guard_enabled(strategy_name: str) -> bool:
    return strategy_name in SHORT_REGIME_GUARD_STRATEGY_NAMES


def resolve_short_regime_guard_state(
    *,
    strategy_name: str,
    recent_closed_trades: Sequence[TradeRecord],
    current_bar_close_time: datetime,
    bar_duration_seconds: int,
) -> tuple[bool, int | None, int | None, int | None, float | None]:
    if not is_short_regime_guard_enabled(strategy_name):
        return False, None, None, None, None
    if bar_duration_seconds <= 0:
        return False, None, None, None, None

    short_closes = _extract_recent_short_closes(
        recent_closed_trades,
        SHORT_REGIME_GUARD_LOOKBACK_SHORT_TRADES,
    )
    if not short_closes:
        return False, 0, None, 0, None

    short_trades_considered = len(short_closes)
    short_take_profit_count = sum(1 for _, close_reason in short_closes if close_reason == "TAKE_PROFIT")
    short_win_rate_pct = (short_take_profit_count / short_trades_considered) * 100
    consecutive_short_stop_losses = _count_consecutive_short_stop_losses(short_closes)
    latest_short_close_time = short_closes[0][0]

    should_activate = (
        short_trades_considered >= SHORT_REGIME_GUARD_MIN_SHORT_TRADES
        and short_win_rate_pct <= SHORT_REGIME_GUARD_MAX_WIN_RATE_PCT
        and consecutive_short_stop_losses >= SHORT_REGIME_GUARD_MIN_CONSECUTIVE_STOP_LOSSES
    )
    if not should_activate:
        return (
            False,
            consecutive_short_stop_losses,
            None,
            short_trades_considered,
            short_win_rate_pct,
        )

    elapsed_seconds = int((current_bar_close_time.astimezone(UTC) - latest_short_close_time).total_seconds())
    if elapsed_seconds < 0:
        return (
            False,
            consecutive_short_stop_losses,
            None,
            short_trades_considered,
            short_win_rate_pct,
        )

    elapsed_bars = elapsed_seconds // bar_duration_seconds
    remaining_bars = SHORT_REGIME_GUARD_BLOCK_BARS - elapsed_bars
    if remaining_bars <= 0:
        return (
            False,
            consecutive_short_stop_losses,
            0,
            short_trades_considered,
            short_win_rate_pct,
        )
    return (
        True,
        consecutive_short_stop_losses,
        remaining_bars,
        short_trades_considered,
        short_win_rate_pct,
    )


def _extract_recent_short_closes(
    recent_closed_trades: Sequence[TradeRecord],
    limit: int,
) -> list[tuple[datetime, str]]:
    closes: list[tuple[datetime, str]] = []
    for trade in recent_closed_trades:
        if trade.get("direction") != "SHORT":
            continue

        close_reason = trade.get("close_reason")
        if close_reason not in ("STOP_LOSS", "TAKE_PROFIT"):
            continue

        close_time = _extract_trade_close_time(trade)
        if close_time is None:
            continue

        closes.append((close_time, close_reason))

    closes.sort(key=lambda item: item[0], reverse=True)
    return closes[:limit]


def _count_consecutive_short_stop_losses(short_closes: Sequence[tuple[datetime, str]]) -> int:
    streak = 0
    for _, close_reason in short_closes:
        if close_reason == "STOP_LOSS":
            streak += 1
            continue
        break
    return streak


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
