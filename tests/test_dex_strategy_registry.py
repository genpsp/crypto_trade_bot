from __future__ import annotations

import unittest

from apps.dex_bot.domain.strategy.registry import resolve_required_history_bars


class ResolveRequiredHistoryBarsTest(unittest.TestCase):
    # gmo_bot 側 2026-05-22 インシデント（cd5b5a8）の同型バグが dex_bot にも残っていたので回帰ガードを敷く
    # strategy.name を v0 → v2 にリネームすると 15m 取得が 600 → 300 に落ち
    # 上位足 EMA slow=34 を計算できず NO_SIGNAL ("UPPER_TREND_EMA_NOT_STABLE") を永久に返す
    # 上位足を導出する戦略を新たに追加する際は必ずここに登録する
    def test_v0_requires_upper_trend_history(self) -> None:
        self.assertEqual(600, resolve_required_history_bars({"name": "ema_trend_pullback_15m_v0"}))

    def test_v2_requires_upper_trend_history(self) -> None:
        self.assertEqual(600, resolve_required_history_bars({"name": "ema_trend_pullback_15m_v2"}))

    def test_default_for_unregistered_strategy(self) -> None:
        self.assertEqual(300, resolve_required_history_bars({"name": "storm_short_v0"}))
        self.assertEqual(300, resolve_required_history_bars({"name": "ema_trend_pullback_v0"}))


if __name__ == "__main__":
    unittest.main()
