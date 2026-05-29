from __future__ import annotations

import unittest

from apps.dex_bot.app.ports.execution_port import SwapConfirmation, SwapSubmission
from apps.dex_bot.app.usecases.open_position import OpenPositionDependencies, OpenPositionInput, open_position
from apps.dex_bot.domain.model.types import BotConfig, EntrySignalDecision, TradeRecord


class _FakeExecution:
    def submit_swap(self, request):
        return SwapSubmission(
            tx_signature="fake_tx",
            in_amount_atomic=20_000_000,
            out_amount_atomic=1_000_000_000,
            result={
                "status": "CONFIRMED",
                "avg_fill_price": 20.0,
                "spent_quote_usdc": 20.0,
                "filled_base_sol": 1.0,
            },
        )

    def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
        return SwapConfirmation(confirmed=True)

    def get_mark_price(self, pair: str) -> float:
        return 20.0

    def get_available_quote_usdc(self, pair: str) -> float:
        return 50.0

    def get_available_base_sol(self, pair: str) -> float:
        return 2.0


class _FakePersistence:
    def __init__(self):
        self.trade: TradeRecord | None = None

    def get_current_config(self):
        raise NotImplementedError

    def create_trade(self, trade: TradeRecord) -> None:
        self.trade = trade

    def update_trade(self, trade_id: str, updates: dict) -> None:
        assert self.trade is not None
        self.trade.update(updates)

    def get_trade(self, trade_id: str):
        return self.trade

    def find_open_trade(self, pair):
        raise NotImplementedError

    def count_trades_for_jst_day(self, pair, jst_day_start_iso, jst_day_end_iso) -> int:
        raise NotImplementedError

    def count_trades_for_utc_day(self, pair, day_start_iso, day_end_iso) -> int:
        raise NotImplementedError

    def list_recent_closed_trades(self, pair, limit):
        raise NotImplementedError

    def save_daily_balance(self, snapshot) -> None:
        raise NotImplementedError

    def list_recent_daily_balances(self, days) -> list:
        raise NotImplementedError

    def save_run(self, run) -> None:
        raise NotImplementedError


class _FakeLock:
    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        pass

    def has_inflight_tx(self, signature: str) -> bool:
        return False

    def clear_inflight_tx(self, signature: str) -> None:
        pass


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warn(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _build_config(variant_id: str | None = None) -> BotConfig:
    meta: dict = {"config_version": 1, "note": "test"}
    if variant_id is not None:
        meta["variant_id"] = variant_id
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 8,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 1.0,
            "max_trades_per_day": 3,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.5,
            "volatile_size_multiplier": 0.8,
            "storm_size_multiplier": 0.4,
        },
        "execution": {
            "mode": "PAPER",
            "swap_provider": "JUPITER",
            "slippage_bps": 15,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
        "meta": meta,  # type: ignore[typeddict-item]
    }


def _build_signal() -> EntrySignalDecision:
    return EntrySignalDecision(
        type="ENTER",
        summary="ENTER",
        ema_fast=19.0,
        ema_slow=18.0,
        entry_price=20.0,
        stop_price=19.5,
        take_profit_price=21.0,
        diagnostics={"position_size_multiplier": 1.0, "volatility_regime": "NORMAL"},
    )


class DexOpenPositionVariantIdTest(unittest.TestCase):
    def _run(self, config: BotConfig):
        persistence = _FakePersistence()
        result = open_position(
            OpenPositionDependencies(
                execution=_FakeExecution(),
                lock=_FakeLock(),
                logger=_FakeLogger(),
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=_build_signal(),
                bar_close_time_iso="2026-05-29T00:00:00Z",
                model_id="dex_test",
            ),
        )
        return result, persistence.trade

    def test_variant_id_snapshot_when_present(self) -> None:
        config = _build_config(variant_id="ema_trend_pullback_15m_v2_20260529")
        result, trade = self._run(config)
        self.assertEqual("OPENED", result.status)
        assert trade is not None
        self.assertEqual("ema_trend_pullback_15m_v2_20260529", trade.get("variant_id"))

    def test_variant_id_empty_string_when_absent(self) -> None:
        config = _build_config(variant_id=None)
        result, trade = self._run(config)
        self.assertEqual("OPENED", result.status)
        assert trade is not None
        self.assertEqual("", trade.get("variant_id"))


if __name__ == "__main__":
    unittest.main()
