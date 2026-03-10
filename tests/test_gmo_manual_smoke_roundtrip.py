from __future__ import annotations

import unittest

from apps.gmo_bot.app.ports.execution_port import SymbolRule
from apps.gmo_bot.app.usecases.manual_smoke_roundtrip import build_smoke_roundtrip_plan


class GmoManualSmokeRoundtripPlanTest(unittest.TestCase):
    def test_build_plan_uses_min_order_size_by_default(self) -> None:
        plan = build_smoke_roundtrip_plan(
            direction="LONG",
            mark_price=14000.0,
            symbol_rule=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
        )

        self.assertEqual("BUY", plan.entry_side)
        self.assertEqual("SELL", plan.close_side)
        self.assertEqual(0.01, plan.size_sol)
        self.assertEqual(140.0, plan.estimated_notional_jpy)

    def test_build_plan_rejects_size_below_min_after_rounding(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "rounds below GMO min_order_size"):
            build_smoke_roundtrip_plan(
                direction="SHORT",
                mark_price=14000.0,
                symbol_rule=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
                requested_size_sol=0.009,
            )

    def test_build_plan_rejects_notional_above_limit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exceeds max_notional_jpy"):
            build_smoke_roundtrip_plan(
                direction="LONG",
                mark_price=14000.0,
                symbol_rule=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
                requested_size_sol=0.05,
                max_notional_jpy=500.0,
            )


if __name__ == "__main__":
    unittest.main()
