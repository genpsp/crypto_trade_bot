from __future__ import annotations

import unittest
from datetime import date, timedelta

from apps.dex_bot.infra.alerting.balance_chart import BalanceChartSeries, render_balance_chart_png
import matplotlib


class BalanceChartTest(unittest.TestCase):
    def test_render_balance_chart_png_returns_png_bytes_for_30_points(self) -> None:
        start = date(2026, 4, 10)
        points = [((start + timedelta(days=index)).isoformat(), 1000.0 + index) for index in range(30)]

        png = render_balance_chart_png(
            title="Balance trend (last 30 days, JST)",
            series=[BalanceChartSeries(label="dex_model", unit="USDC", points=points)],
            target_date_jst="2026-05-09",
        )

        self.assertIsNotNone(png)
        assert png is not None
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 1000)

    def test_render_balance_chart_png_returns_none_for_empty_series(self) -> None:
        self.assertIsNone(
            render_balance_chart_png(
                title="Balance trend (last 30 days, JST)",
                series=[BalanceChartSeries(label="dex_model", unit="USDC", points=[])],
                target_date_jst="2026-05-09",
            )
        )

    def test_matplotlib_uses_agg_backend(self) -> None:
        self.assertEqual("agg", matplotlib.get_backend().lower())


if __name__ == "__main__":
    unittest.main()
