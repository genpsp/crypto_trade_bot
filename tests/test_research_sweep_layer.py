from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.market_dataset import MarketDataset
from research.src.eval.trial import TrialResult
from research.src.eval.window import build_windows
from research.src.sweep.grid import expand_cases, format_case_name
from research.src.sweep.overrides import apply_overrides
from research.src.sweep.plan import build_plan
from research.src.sweep.spec_loader import SweepSpec


def _build_bars(count: int, *, step_minutes: int = 15) -> list[OhlcvBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    for index in range(count):
        open_time = start + timedelta(minutes=step_minutes * index)
        close_time = open_time + timedelta(minutes=step_minutes)
        price = 100.0 + index * 0.01
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=price,
                high=price + 0.1,
                low=price - 0.1,
                close=price,
                volume=1000.0,
            )
        )
    return bars


class ResearchSweepLayerTest(unittest.TestCase):
    def test_expand_full_grid_cases(self) -> None:
        cases = expand_cases(
            [
                {"path": "risk.max_trades_per_day", "values": [2, 3]},
                {"path": "exit.take_profit_r_multiple", "values": [1.6, 1.8]},
            ]
        )
        self.assertEqual(4, len(cases))
        self.assertEqual("exit.take_profit_r_multiple=1.6,risk.max_trades_per_day=2", format_case_name(cases[0]))

    def test_apply_overrides_uses_dotted_path_without_mutating_base(self) -> None:
        base = {"risk": {"max_trades_per_day": 4}, "exit": {"take_profit_r_multiple": 1.8}}
        updated = apply_overrides(base, {"risk.max_trades_per_day": 2})
        self.assertEqual(2, updated["risk"]["max_trades_per_day"])
        self.assertEqual(4, base["risk"]["max_trades_per_day"])

    def test_build_windows_supports_last_n_days_and_walk_forward(self) -> None:
        dataset = MarketDataset.from_bars(
            broker="GMO_COIN",
            pair="SOL/JPY",
            timeframe="15m",
            bars=_build_bars(96 * 12),
        )
        windows = build_windows(
            [
                {"type": "last_n_days", "days": 3},
                {"type": "walk_forward", "train_days": 5, "test_days": 2, "step_days": 2, "max_windows": 2},
            ],
            dataset,
        )
        self.assertEqual(5, len(windows))
        self.assertEqual("last_n_days", windows[0].type)
        self.assertEqual(["train", "test", "train", "test"], [window.role for window in windows[1:]])

    def test_trial_result_to_dict_omits_trades_by_default(self) -> None:
        result = TrialResult(
            trial_id="t1",
            summary={},
            no_signal_reason_counts={},
            runtime_seconds=0.1,
            trades=[{"exit_reason": "TAKE_PROFIT"}],
        )
        self.assertNotIn("trades", result.to_dict())
        self.assertIn("trades", result.to_dict(include_trades=True))

    def test_build_plan_creates_deterministic_trial_specs(self) -> None:
        dataset = MarketDataset.from_bars(
            broker="GMO_COIN",
            pair="SOL/JPY",
            timeframe="15m",
            bars=_build_bars(96 * 3),
        )
        spec = SweepSpec(
            name="unit_sweep",
            model_id="gmo_ema_pullback_15m_both_v0",
            base_config="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",  # type: ignore[arg-type]
            dataset={"broker": "GMO_COIN", "pair": "SOL/JPY", "timeframe": "15m"},
            windows=[{"type": "last_n_days", "days": 1}],
            axes=[{"path": "risk.max_trades_per_day", "values": [2, 3]}],
            combinations="full_grid",
            cases=[],
            source_path="unit.yaml",  # type: ignore[arg-type]
        )
        first = build_plan(spec, dataset)
        second = build_plan(spec, dataset)
        self.assertEqual(2, len(first))
        self.assertEqual([trial.trial_id for trial in first], [trial.trial_id for trial in second])
        self.assertEqual(2, first[0].config["risk"]["max_trades_per_day"])


if __name__ == "__main__":
    unittest.main()
