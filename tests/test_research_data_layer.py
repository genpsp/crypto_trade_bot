from __future__ import annotations

from datetime import UTC, datetime, timedelta
import tempfile
import unittest

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.market_dataset import MarketDataset, compute_data_hash
from research.src.data.partitioned_cache import (
    PartitionedOhlcvCache,
    broker_to_safe,
    detect_gaps,
    is_pyarrow_available,
    pair_to_safe,
    timeframe_to_timedelta,
)


def _build_bars(count: int, *, start: datetime | None = None, step_minutes: int = 15) -> list[OhlcvBar]:
    resolved_start = start or datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    for index in range(count):
        open_time = resolved_start + timedelta(minutes=step_minutes * index)
        close_time = open_time + timedelta(minutes=step_minutes)
        price = 100.0 + index
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.5,
                volume=1000.0 + index,
            )
        )
    return bars


class ResearchDataHelpersTest(unittest.TestCase):
    def test_safe_tokens_match_cache_path_convention(self) -> None:
        self.assertEqual("soljpy", pair_to_safe("SOL/JPY"))
        self.assertEqual("solusdc", pair_to_safe("SOL/USDC"))
        self.assertEqual("gmo", broker_to_safe("GMO_COIN"))
        self.assertEqual("dex", broker_to_safe("DEX"))

    def test_timeframe_to_timedelta(self) -> None:
        self.assertEqual(timedelta(minutes=15), timeframe_to_timedelta("15m"))
        self.assertEqual(timedelta(hours=2), timeframe_to_timedelta("2h"))

    def test_market_dataset_slice_and_hash(self) -> None:
        bars = _build_bars(5)
        dataset = MarketDataset.from_bars(
            broker="DEX",
            pair="SOL/USDC",
            timeframe="15m",
            bars=bars,
        )

        sliced = dataset.slice(bars[1].close_time, bars[3].close_time)

        self.assertEqual(3, len(sliced.bars))
        self.assertEqual(compute_data_hash(sliced.bars), sliced.data_hash)
        self.assertNotEqual(dataset.data_hash, sliced.data_hash)

    def test_detect_gaps_reports_missing_bars(self) -> None:
        bars = _build_bars(2)
        bars.append(_build_bars(1, start=datetime(2026, 1, 1, 1, 0, tzinfo=UTC))[0])

        gaps = detect_gaps(bars, "15m")

        self.assertEqual(1, len(gaps))
        self.assertEqual(2, gaps[0]["missing_bars"])


@unittest.skipUnless(is_pyarrow_available(), "pyarrow is not installed")
class PartitionedOhlcvCacheTest(unittest.TestCase):
    def test_append_load_dedup_and_manifest(self) -> None:
        start = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        bars = _build_bars(4, start=start)
        duplicate = _build_bars(1, start=bars[-1].open_time)[0]
        duplicate.close = 999.0

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PartitionedOhlcvCache(tmpdir)
            manifest = cache.append_bars(
                broker="DEX",
                pair="SOL/USDC",
                timeframe="15m",
                bars=bars,
                source="test",
                fetched_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
            cache.append_bars(
                broker="DEX",
                pair="SOL/USDC",
                timeframe="15m",
                bars=[duplicate],
                source="test",
                fetched_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
            loaded = cache.load_bars(broker="DEX", pair="SOL/USDC", timeframe="15m")

        self.assertEqual(4, len(loaded))
        self.assertEqual(999.0, loaded[-1].close)
        self.assertEqual(4, manifest["bar_count"])


if __name__ == "__main__":
    unittest.main()
