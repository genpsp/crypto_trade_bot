from __future__ import annotations

import unittest
from copy import deepcopy

from pybot.infra.config.schema import parse_config


def _build_base_config() -> dict:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG_ONLY",
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
    def test_model_wallet_key_path_is_parsed_when_present(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["models"] = [
            {
                "model_id": "core_long_v0",
                "enabled": True,
                "direction": "LONG_ONLY",
                "wallet_key_path": "secrets/wallet.long.enc.json",
                "strategy": payload["strategy"],
                "risk": payload["risk"],
                "exit": payload["exit"],
            }
        ]
        parsed = parse_config(payload)
        self.assertEqual(parsed["models"][0]["wallet_key_path"], "secrets/wallet.long.enc.json")

    def test_model_wallet_key_path_must_not_be_empty(self) -> None:
        payload = deepcopy(_build_base_config())
        payload["models"] = [
            {
                "model_id": "core_long_v0",
                "enabled": True,
                "direction": "LONG_ONLY",
                "wallet_key_path": "   ",
                "strategy": payload["strategy"],
                "risk": payload["risk"],
                "exit": payload["exit"],
            }
        ]
        with self.assertRaisesRegex(ValueError, "models\\[0\\]\\.wallet_key_path"):
            parse_config(payload)


if __name__ == "__main__":
    unittest.main()
