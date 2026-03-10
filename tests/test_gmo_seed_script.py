from __future__ import annotations

import runpy
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(PROJECT_ROOT / "scripts" / "seed-gmo-firestore-config.py"))


class GmoSeedScriptTest(unittest.TestCase):
    def test_build_default_model_configs(self) -> None:
        configs = MODULE["build_default_model_configs"]("LIVE")
        self.assertEqual(sorted(configs.keys()), [
            "gmo_ema_pullback_15m_both_v0",
            "gmo_ema_pullback_2h_long_v0",
            "gmo_storm_2h_short_v0",
        ])
        self.assertEqual(configs["gmo_ema_pullback_2h_long_v0"]["pair"], "SOL/JPY")
        self.assertEqual(configs["gmo_ema_pullback_15m_both_v0"]["execution"]["slippage_bps"], 3)
        self.assertFalse(configs["gmo_ema_pullback_2h_long_v0"]["enabled"])
        self.assertFalse(configs["gmo_storm_2h_short_v0"]["enabled"])
        self.assertTrue(configs["gmo_ema_pullback_15m_both_v0"]["enabled"])

    def test_build_model_doc_payload(self) -> None:
        config = MODULE["build_default_model_configs"]("PAPER")["gmo_ema_pullback_2h_long_v0"]
        payload = MODULE["_build_model_doc_payload"]("gmo_ema_pullback_2h_long_v0", config)
        self.assertEqual(payload["model_id"], "gmo_ema_pullback_2h_long_v0")
        self.assertEqual(payload["mode"], "PAPER")
        self.assertEqual(payload["broker"], "GMO_COIN")

    def test_normalize_model_id_adds_prefix(self) -> None:
        normalized = MODULE["_normalize_model_id"]("ema_pullback_2h_long_v0")
        self.assertEqual(normalized, "gmo_ema_pullback_2h_long_v0")


if __name__ == "__main__":
    unittest.main()
