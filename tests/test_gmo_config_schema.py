from __future__ import annotations

import unittest

from apps.gmo_bot.infra.config.schema import parse_config


class GmoConfigSchemaTest(unittest.TestCase):
    def _valid_config(self) -> dict:
        return {
            "enabled": True,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
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
                "broker": "GMO_COIN",
                "slippage_bps": 3,
                "min_notional_jpy": 5000,
                "leverage_multiplier": 1.0,
                "margin_usage_ratio": 0.99,
            },
            "exit": {
                "stop": "SWING_LOW",
                "take_profit_r_multiple": 2.4,
            },
            "meta": {
                "config_version": 1,
                "note": "test",
            },
        }

    def test_parse_valid_config(self) -> None:
        parsed = parse_config(self._valid_config())
        self.assertEqual(parsed["pair"], "SOL/JPY")
        self.assertEqual(parsed["execution"]["broker"], "GMO_COIN")
        self.assertEqual(parsed["execution"]["min_notional_jpy"], 5000.0)

    def test_rejects_invalid_pair(self) -> None:
        config = self._valid_config()
        config["pair"] = "SOL/USDC"
        with self.assertRaises(ValueError):
            parse_config(config)

    def test_rejects_invalid_leverage(self) -> None:
        config = self._valid_config()
        config["execution"]["leverage_multiplier"] = 2.5
        with self.assertRaises(ValueError):
            parse_config(config)


if __name__ == "__main__":
    unittest.main()
