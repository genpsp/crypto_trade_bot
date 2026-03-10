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
            "ema_pullback_15m_both_v0",
            "ema_pullback_2h_long_v0",
            "storm_2h_short_v0",
        ])
        self.assertEqual(configs["ema_pullback_2h_long_v0"]["pair"], "SOL/JPY")
        self.assertEqual(configs["ema_pullback_15m_both_v0"]["execution"]["slippage_bps"], 3)

    def test_build_model_doc_payload(self) -> None:
        config = MODULE["build_default_model_configs"]("PAPER")["ema_pullback_2h_long_v0"]
        payload = MODULE["_build_model_doc_payload"]("ema_pullback_2h_long_v0", config)
        self.assertEqual(payload["model_id"], "ema_pullback_2h_long_v0")
        self.assertEqual(payload["mode"], "PAPER")
        self.assertEqual(payload["broker"], "GMO_COIN")


if __name__ == "__main__":
    unittest.main()
