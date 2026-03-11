from __future__ import annotations

import unittest

from apps.dex_bot.adapters.market_data.ohlcv_provider import OhlcvProvider as DexOhlcvProvider
from apps.gmo_bot.adapters.market_data.ohlcv_provider import OhlcvProvider as GmoOhlcvProvider
from research.scripts.fetch_ohlcv import _build_provider
from research.src.infra.research_config import load_bot_config


class ResearchConfigTest(unittest.TestCase):
    def test_load_bot_config_preserves_dex_config_shape(self) -> None:
        config = load_bot_config("research/models/ema_pullback_15m_both_v0/config/current.json")

        self.assertEqual("SOL/USDC", config["pair"])
        self.assertEqual(20.0, config["execution"]["min_notional_usdc"])
        self.assertEqual("JUPITER", config["execution"]["swap_provider"])

    def test_load_bot_config_normalizes_gmo_config_for_shared_backtest_engine(self) -> None:
        config = load_bot_config("research/models/gmo_ema_pullback_15m_both_v0/config/current.json")

        self.assertEqual("SOL/JPY", config["pair"])
        self.assertEqual("GMO_COIN", config["broker"])
        self.assertEqual("GMO_COIN", config["execution"]["broker"])
        self.assertEqual("GMO_COIN", config["execution"]["swap_provider"])
        self.assertEqual(5000.0, config["execution"]["min_notional_usdc"])
        self.assertEqual(5000.0, config["execution"]["min_notional_jpy"])
        self.assertEqual(1_000_000.0, config["execution"]["initial_quote_balance"])


class ResearchFetchOhlcvProviderTest(unittest.TestCase):
    def test_build_provider_returns_dex_provider_for_solusdc(self) -> None:
        provider = _build_provider("SOL/USDC")
        self.assertIsInstance(provider, DexOhlcvProvider)

    def test_build_provider_returns_gmo_provider_for_soljpy(self) -> None:
        provider = _build_provider("SOL/JPY")
        self.assertIsInstance(provider, GmoOhlcvProvider)


if __name__ == "__main__":
    unittest.main()
