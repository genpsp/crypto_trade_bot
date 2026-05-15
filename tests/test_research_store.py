from __future__ import annotations

import tempfile
import unittest

from research.src.store.lineage import build_run_id
from research.src.store.trial_store import TrialStore, flatten_trial_row, unflatten_trial_row
from research.src.store.views import diff, marginal_by_axis, rank


class ResearchStoreTest(unittest.TestCase):
    def test_flatten_roundtrip_preserves_summary_and_tags(self) -> None:
        row = {
            "trial_id": "t1",
            "model_id": "m1",
            "config": {"risk": {"max_trades_per_day": 2}},
            "dataset_key": {"broker": "GMO_COIN"},
            "window": {"window_id": "w1"},
            "tags": {"case_name": "baseline", "axis_values": {"risk.max_trades_per_day": 2}},
            "summary": {"return_to_dd": 1.2, "position_size_multiplier_counts": {"1.0": 3}},
            "no_signal_reason_counts": {"A": 1},
            "runtime_seconds": 0.1,
            "error": None,
        }
        restored = unflatten_trial_row(flatten_trial_row(row))
        self.assertEqual("baseline", restored["tags"]["case_name"])
        self.assertEqual(2, restored["config"]["risk"]["max_trades_per_day"])
        self.assertEqual(1.2, restored["summary"]["return_to_dd"])
        self.assertEqual({"A": 1}, restored["no_signal_reason_counts"])

    def test_trial_store_write_and_load_run(self) -> None:
        rows = [
            {
                "trial_id": "t1",
                "model_id": "m1",
                "config": {"risk": {"max_trades_per_day": 2}},
                "dataset_key": {"broker": "GMO_COIN"},
                "window": {"window_id": "w1"},
                "tags": {"case_name": "baseline", "axis_values": {}},
                "summary": {"return_to_dd": 2.0, "total_scaled_pnl_pct": 10.0},
                "no_signal_reason_counts": {},
                "runtime_seconds": 0.1,
                "error": None,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrialStore(tmpdir)
            store.write_run(run_id="20260101-test-abcdef0", manifest={"run_id": "20260101-test-abcdef0"}, rows=rows)
            self.assertEqual("20260101-test-abcdef0", store.latest_run_id())
            loaded_manifest = store.load_manifest("latest")
            loaded_rows = store.load_trials("latest")
        self.assertEqual("20260101-test-abcdef0", loaded_manifest["run_id"])
        self.assertEqual("t1", loaded_rows[0]["trial_id"])
        self.assertEqual(2.0, loaded_rows[0]["summary"]["return_to_dd"])

    def test_trial_store_write_and_load_trades(self) -> None:
        rows = [
            {
                "trial_id": "t1",
                "model_id": "m1",
                "config": {},
                "dataset_key": {},
                "window": {},
                "tags": {},
                "summary": {},
                "no_signal_reason_counts": {},
                "runtime_seconds": 0.1,
                "error": None,
            }
        ]
        trades = {
            "t1": [
                {
                    "entry_time": "2026-01-01T00:00:00Z",
                    "exit_time": "2026-01-01T01:00:00Z",
                    "exit_reason": "TAKE_PROFIT",
                    "scaled_pnl_pct": 1.25,
                    "r_multiple": 1.8,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrialStore(tmpdir)
            store.write_run(
                run_id="20260101-test-abcdef0",
                manifest={"run_id": "20260101-test-abcdef0"},
                rows=rows,
                trades_by_trial_id=trades,
            )
            loaded_trades = store.load_trades("latest", "t1")
        self.assertEqual(1, len(loaded_trades))
        self.assertEqual("TAKE_PROFIT", loaded_trades[0]["exit_reason"])
        self.assertEqual(1.25, loaded_trades[0]["scaled_pnl_pct"])

    def test_views_rank_marginal_and_diff(self) -> None:
        rows_a = [
            {"trial_id": "a", "tags": {"case_name": "x", "axis_values": {"axis": "1"}}, "summary": {"return_to_dd": 1.0}, "error": None},
            {"trial_id": "b", "tags": {"case_name": "y", "axis_values": {"axis": "2"}}, "summary": {"return_to_dd": 3.0}, "error": None},
        ]
        rows_b = [
            {"trial_id": "c", "tags": {"case_name": "x", "axis_values": {"axis": "1"}}, "summary": {"return_to_dd": 2.0}, "error": None},
        ]
        self.assertEqual("b", rank(rows_a, by="return_to_dd", top_k=1)[0]["trial_id"])
        self.assertEqual(2, len(marginal_by_axis(rows_a, metric="return_to_dd")))
        diff_rows = diff(rows_a, rows_b, metric="return_to_dd")
        self.assertEqual(1.0, next(row for row in diff_rows if row["key"] == "x")["delta"])

    def test_build_run_id_contains_spec_slug_and_git_suffix(self) -> None:
        run_id = build_run_id("My Spec", git_sha="abcdef012345")
        self.assertIn("my-spec", run_id)
        self.assertTrue(run_id.endswith("abcdef0"))


if __name__ == "__main__":
    unittest.main()
