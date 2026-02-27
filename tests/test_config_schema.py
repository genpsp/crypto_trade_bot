from __future__ import annotations

import unittest
from copy import deepcopy

from pybot.infra.config.schema import parse_config


def _build_base_config() -> dict:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "2h",
        "strategy": {
            "name": "ema_trend_pullback_v0",
            "ema_fast_period": 5,
            "ema_slow_period": 13,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 3.0,
            "max_trades_per_day": 2,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.75,
            "storm_size_multiplier": 0.5,
        },
        "execution": {
            "mode": "LIVE",
            "swap_provider": "JUPITER",
            "slippage_bps": 15,
            "min_notional_usdc": 20,
            "only_direct_routes": False,
        },
        "exit": {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 1.5,
        },
        "meta": {
            "config_version": 2,
            "note": "schema test",
        },
    }


class ConfigSchemaTest(unittest.TestCase):
    def test_15m_timeframe_is_allowed(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["signal_timeframe"] = "15m"
        payload["strategy"]["name"] = "ema_trend_pullback_15m_v0"
        parsed = parse_config(payload)
        self.assertEqual(parsed["signal_timeframe"], "15m")

    def test_ema_trend_pullback_15m_strategy_name_is_allowed(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["signal_timeframe"] = "15m"
        payload["strategy"]["name"] = "ema_trend_pullback_15m_v0"
        parsed = parse_config(payload)
        self.assertEqual(parsed["strategy"]["name"], "ema_trend_pullback_15m_v0")

    def test_ema_trend_pullback_15m_strategy_rejects_non_15m_timeframe(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["signal_timeframe"] = "2h"
        payload["strategy"]["name"] = "ema_trend_pullback_15m_v0"
        with self.assertRaisesRegex(ValueError, "requires signal_timeframe='15m'"):
            parse_config(payload)

    def test_ema_trend_pullback_v0_rejects_15m_timeframe(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["signal_timeframe"] = "15m"
        payload["strategy"]["name"] = "ema_trend_pullback_v0"
        with self.assertRaisesRegex(ValueError, "requires signal_timeframe='2h' or '4h'"):
            parse_config(payload)

    def test_storm_size_multiplier_zero_is_allowed(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["risk"]["storm_size_multiplier"] = 0.0
        parsed = parse_config(payload)
        self.assertEqual(parsed["risk"]["storm_size_multiplier"], 0.0)

    def test_models_key_is_rejected(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["models"] = [
            {
                "model_id": "ema_pullback_2h_long_v0",
                "enabled": True,
                "direction": "LONG",
                "wallet_key_path": "secrets/wallet.long.enc.json",
                "strategy": payload["strategy"],
                "risk": payload["risk"],
                "exit": payload["exit"],
            }
        ]
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            parse_config(payload)


if __name__ == "__main__":
    unittest.main()
