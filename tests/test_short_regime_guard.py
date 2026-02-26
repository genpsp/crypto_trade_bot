from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from pybot.domain.model.types import TradeRecord
from pybot.domain.risk.short_regime_guard import (
    SHORT_REGIME_GUARD_BLOCK_BARS,
    resolve_short_regime_guard_state,
)


def _build_short_trade(*, close_reason: str, close_time: datetime) -> TradeRecord:
    close_time_iso = close_time.isoformat().replace("+00:00", "Z")
    return {
        "pair": "SOL/USDC",
        "state": "CLOSED",
        "direction": "SHORT",
        "close_reason": close_reason,
        "position": {"exit_time_iso": close_time_iso},
        "updated_at": close_time_iso,
    }


class ShortRegimeGuardTest(unittest.TestCase):
    def test_activates_when_short_stops_accumulate_with_poor_win_rate(self) -> None:
        now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
        recent_closed_trades: list[TradeRecord] = [
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 48)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 49)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 50)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 51)),
            _build_short_trade(close_reason="TAKE_PROFIT", close_time=now - timedelta(minutes=15 * 52)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 53)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 54)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 55)),
        ]

        active, consecutive_losses, remaining_bars, recent_short_trades, recent_short_win_rate_pct = (
            resolve_short_regime_guard_state(
                strategy_name="ema_trend_pullback_15m_v0",
                recent_closed_trades=recent_closed_trades,
                current_bar_close_time=now,
                bar_duration_seconds=15 * 60,
            )
        )

        self.assertTrue(active)
        self.assertEqual(4, consecutive_losses)
        self.assertEqual(8, recent_short_trades)
        self.assertEqual(12.5, recent_short_win_rate_pct)
        self.assertEqual(48, remaining_bars)

    def test_does_not_activate_when_consecutive_short_losses_are_insufficient(self) -> None:
        now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
        recent_closed_trades: list[TradeRecord] = [
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 6)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 7)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 8)),
            _build_short_trade(close_reason="TAKE_PROFIT", close_time=now - timedelta(minutes=15 * 9)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 10)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 11)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 12)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=now - timedelta(minutes=15 * 13)),
        ]

        active, consecutive_losses, remaining_bars, recent_short_trades, recent_short_win_rate_pct = (
            resolve_short_regime_guard_state(
                strategy_name="ema_trend_pullback_15m_v0",
                recent_closed_trades=recent_closed_trades,
                current_bar_close_time=now,
                bar_duration_seconds=15 * 60,
            )
        )

        self.assertFalse(active)
        self.assertEqual(3, consecutive_losses)
        self.assertEqual(8, recent_short_trades)
        self.assertEqual(12.5, recent_short_win_rate_pct)
        self.assertIsNone(remaining_bars)

    def test_expires_after_block_window(self) -> None:
        latest_close = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        now = latest_close + timedelta(minutes=15 * (SHORT_REGIME_GUARD_BLOCK_BARS + 1))
        recent_closed_trades: list[TradeRecord] = [
            _build_short_trade(close_reason="STOP_LOSS", close_time=latest_close),
            _build_short_trade(close_reason="STOP_LOSS", close_time=latest_close - timedelta(minutes=15)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=latest_close - timedelta(minutes=30)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=latest_close - timedelta(minutes=45)),
            _build_short_trade(close_reason="TAKE_PROFIT", close_time=latest_close - timedelta(minutes=60)),
            _build_short_trade(close_reason="STOP_LOSS", close_time=latest_close - timedelta(minutes=75)),
        ]

        active, consecutive_losses, remaining_bars, recent_short_trades, recent_short_win_rate_pct = (
            resolve_short_regime_guard_state(
                strategy_name="ema_trend_pullback_15m_v0",
                recent_closed_trades=recent_closed_trades,
                current_bar_close_time=now,
                bar_duration_seconds=15 * 60,
            )
        )

        self.assertFalse(active)
        self.assertEqual(4, consecutive_losses)
        self.assertEqual(6, recent_short_trades)
        self.assertAlmostEqual(16.6667, float(recent_short_win_rate_pct), places=3)
        self.assertEqual(0, remaining_bars)

    def test_disabled_for_non_target_strategy(self) -> None:
        active, consecutive_losses, remaining_bars, recent_short_trades, recent_short_win_rate_pct = (
            resolve_short_regime_guard_state(
                strategy_name="ema_trend_pullback_v0",
                recent_closed_trades=[],
                current_bar_close_time=datetime(2026, 2, 1, tzinfo=UTC),
                bar_duration_seconds=15 * 60,
            )
        )

        self.assertFalse(active)
        self.assertIsNone(consecutive_losses)
        self.assertIsNone(remaining_bars)
        self.assertIsNone(recent_short_trades)
        self.assertIsNone(recent_short_win_rate_pct)


if __name__ == "__main__":
    unittest.main()
