from __future__ import annotations

import unittest
from copy import deepcopy

from apps.dex_bot.infra.config.schema import parse_config


def _build_base_config() -> dict:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "BOTH",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 8,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 1.2,
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
            "min_notional_usdc": 20,
            "only_direct_routes": False,
        },
        "exit": {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 2.0,
        },
        "meta": {
            "config_version": 1,
            "note": "schema test",
        },
    }


class DexConfigSchemaVariantIdTest(unittest.TestCase):
    def test_meta_variant_id_accepted_when_present(self) -> None:
        config = deepcopy(_build_base_config())
        config["meta"]["variant_id"] = "ema_trend_pullback_15m_v2_20260529"
        parsed = parse_config(config)
        self.assertEqual("ema_trend_pullback_15m_v2_20260529", parsed["meta"].get("variant_id"))

    def test_meta_variant_id_absent_is_ok(self) -> None:
        config = deepcopy(_build_base_config())
        parsed = parse_config(config)
        self.assertNotIn("variant_id", parsed["meta"])

    def test_meta_variant_id_empty_string_is_rejected(self) -> None:
        config = deepcopy(_build_base_config())
        config["meta"]["variant_id"] = ""
        with self.assertRaises(ValueError):
            parse_config(config)

    def test_meta_variant_id_non_string_is_rejected(self) -> None:
        config = deepcopy(_build_base_config())
        config["meta"]["variant_id"] = 123
        with self.assertRaises(ValueError):
            parse_config(config)


if __name__ == "__main__":
    unittest.main()
