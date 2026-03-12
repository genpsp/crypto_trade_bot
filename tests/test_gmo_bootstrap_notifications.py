from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.gmo_bot.infra.bootstrap import bootstrap


class _FakeLogger:
    def info(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context


class _FakeEnv:
    GOOGLE_APPLICATION_CREDENTIALS = "/tmp/firebase.json"
    REDIS_URL = "redis://localhost:6379/0"
    GMO_API_KEY = "key"
    GMO_API_SECRET = "secret"
    SLACK_WEBHOOK_URL = "https://hooks.slack.test/services/a/b/c"


class _FakeSnapshotHandle:
    def unsubscribe(self) -> None:
        return None


class _FakeFirestoreNode:
    def collection(self, _name: str) -> "_FakeFirestoreNode":
        return self

    def document(self, _name: str) -> "_FakeFirestoreNode":
        return self

    def on_snapshot(self, _callback) -> _FakeSnapshotHandle:
        return _FakeSnapshotHandle()


class _FakeFirestoreClient(_FakeFirestoreNode):
    @classmethod
    def from_service_account_json(cls, _path: str) -> "_FakeFirestoreClient":
        return cls()


class _FakeConfigRepo:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def list_model_ids(self) -> list[str]:
        return ["gmo_ema_pullback_15m_both_v0"]

    def get_model_metadata(self, _model_id: str) -> SimpleNamespace:
        return SimpleNamespace(mode="LIVE", direction="BOTH")

    def get_current_config(self, _model_id: str) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "pair": "SOL/JPY",
            "strategy": {"name": "ema_trend_pullback_15m_v0"},
            "broker": "GMO_COIN",
        }

    def is_global_pause_enabled(self) -> bool:
        return False


class _FakePersistence:
    def __init__(self, trade: dict[str, object] | None = None) -> None:
        self._trade = trade

    def find_open_trade(self, _pair: str):
        return None

    def get_trade(self, _trade_id: str):
        return self._trade


class _FakeCronController:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class _FakeThread:
    def __init__(self, *args, **kwargs) -> None:
        _ = args
        _ = kwargs

    def start(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        _ = timeout
        return None


class _StrictFakeNotifier:
    instances: list["_StrictFakeNotifier"] = []

    def __init__(self, *, config, logger, dedupe_store=None, dedupe_namespace: str = "bot") -> None:
        _ = config
        _ = logger
        _ = dedupe_store
        _ = dedupe_namespace
        self.trade_errors: list[dict[str, object]] = []
        self.trade_closed: list[dict[str, object]] = []
        self.startup_payloads: list[list[dict[str, str]]] = []
        self.shutdown_reasons: list[str] = []
        self.consecutive_failures: list[dict[str, object]] = []
        self.recovered: list[dict[str, object]] = []
        _StrictFakeNotifier.instances.append(self)

    @property
    def enabled(self) -> bool:
        return True

    def notify_startup(self, models: list[dict[str, str]]) -> None:
        self.startup_payloads.append(models)

    def notify_shutdown(self, *, reason: str) -> None:
        self.shutdown_reasons.append(reason)

    def notify_trade_error(
        self,
        *,
        model_id: str,
        result: str,
        summary: str,
        run_id: str | None,
        trade_id: str | None,
    ) -> None:
        self.trade_errors.append(
            {
                "model_id": model_id,
                "result": result,
                "summary": summary,
                "run_id": run_id,
                "trade_id": trade_id,
            }
        )

    def notify_trade_closed(
        self,
        *,
        model_id: str,
        trade_id: str,
        pair: str,
        direction: str,
        close_reason: str,
        entry_price: float | None,
        exit_price: float | None,
        gross_pnl: float | None,
        fee: float | None,
        net_pnl: float | None,
        quote_ccy: str,
    ) -> None:
        self.trade_closed.append(
            {
                "model_id": model_id,
                "trade_id": trade_id,
                "pair": pair,
                "direction": direction,
                "close_reason": close_reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "gross_pnl": gross_pnl,
                "fee": fee,
                "net_pnl": net_pnl,
                "quote_ccy": quote_ccy,
            }
        )

    def notify_consecutive_failures(
        self,
        *,
        model_id: str,
        streak: int,
        threshold: int,
        run_id: str | None,
        summary: str,
    ) -> None:
        self.consecutive_failures.append(
            {
                "model_id": model_id,
                "streak": streak,
                "threshold": threshold,
                "run_id": run_id,
                "summary": summary,
            }
        )

    def notify_failure_streak_recovered(
        self,
        *,
        model_id: str,
        previous_streak: int,
        latest_result: str,
        summary: str,
    ) -> None:
        self.recovered.append(
            {
                "model_id": model_id,
                "previous_streak": previous_streak,
                "latest_result": latest_result,
                "summary": summary,
            }
        )

    def notify_stale_cycle(self, *, elapsed_seconds: int, threshold_minutes: int, model_ids: list[str]) -> None:
        _ = elapsed_seconds
        _ = threshold_minutes
        _ = model_ids

    def notify_stale_cycle_recovered(self, *, model_ids: list[str]) -> None:
        _ = model_ids


class GmoBootstrapNotificationsTest(unittest.TestCase):
    def test_closed_cycle_notifies_trade_close_for_take_profit(self) -> None:
        _StrictFakeNotifier.instances.clear()
        closed_trade = {
            "trade_id": "trade_1",
            "pair": "SOL/JPY",
            "direction": "SHORT",
            "close_reason": "TAKE_PROFIT",
            "position": {
                "entry_price": 13500.0,
                "exit_price": 13400.0,
                "quote_amount_jpy": 6750.0,
                "quantity_sol": 0.5,
            },
            "execution": {
                "entry_fee_jpy": 3.0,
                "exit_fee_jpy": 3.0,
                "exit_result": {"filled_quote_jpy": 6700.0},
            },
        }
        with (
            patch("apps.gmo_bot.infra.bootstrap.load_env", return_value=_FakeEnv()),
            patch("apps.gmo_bot.infra.bootstrap.create_logger", return_value=_FakeLogger()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreClient", _FakeFirestoreClient),
            patch("apps.gmo_bot.infra.bootstrap.Redis.from_url", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreConfigRepository", return_value=_FakeConfigRepo()),
            patch("apps.gmo_bot.infra.bootstrap.GmoApiClient", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.OhlcvProvider", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.PaperExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.GmoMarginExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreRepository", return_value=_FakePersistence(closed_trade)),
            patch("apps.gmo_bot.infra.bootstrap.RedisLockAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.SlackNotifier", _StrictFakeNotifier),
            patch("apps.gmo_bot.infra.bootstrap.create_cron_cycle", return_value=_FakeCronController()),
            patch(
                "apps.gmo_bot.infra.bootstrap.run_cycle",
                return_value={
                    "result": "CLOSED",
                    "summary": "CLOSED: reason=TAKE_PROFIT",
                    "run_id": "run_1",
                    "trade_id": "trade_1",
                },
            ),
            patch("apps.gmo_bot.infra.bootstrap.threading.Thread", _FakeThread),
            patch("apps.gmo_bot.infra.bootstrap._should_execute_cycle", return_value=True),
        ):
            runtime = bootstrap()
            runtime.start()
            runtime.stop()

        notifier = _StrictFakeNotifier.instances[0]
        self.assertEqual(1, len(notifier.trade_closed))
        self.assertEqual("trade_1", notifier.trade_closed[0]["trade_id"])
        self.assertEqual("TAKE_PROFIT", notifier.trade_closed[0]["close_reason"])
        self.assertEqual("JPY", notifier.trade_closed[0]["quote_ccy"])
        self.assertAlmostEqual(50.0, notifier.trade_closed[0]["gross_pnl"])
        self.assertAlmostEqual(6.0, notifier.trade_closed[0]["fee"])
        self.assertAlmostEqual(44.0, notifier.trade_closed[0]["net_pnl"])

    def test_failed_cycle_uses_current_notifier_signatures(self) -> None:
        _StrictFakeNotifier.instances.clear()
        with (
            patch("apps.gmo_bot.infra.bootstrap.load_env", return_value=_FakeEnv()),
            patch("apps.gmo_bot.infra.bootstrap.create_logger", return_value=_FakeLogger()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreClient", _FakeFirestoreClient),
            patch("apps.gmo_bot.infra.bootstrap.Redis.from_url", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreConfigRepository", return_value=_FakeConfigRepo()),
            patch("apps.gmo_bot.infra.bootstrap.GmoApiClient", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.OhlcvProvider", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.PaperExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.GmoMarginExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreRepository", return_value=_FakePersistence()),
            patch("apps.gmo_bot.infra.bootstrap.RedisLockAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.SlackNotifier", _StrictFakeNotifier),
            patch("apps.gmo_bot.infra.bootstrap.create_cron_cycle", return_value=_FakeCronController()),
            patch(
                "apps.gmo_bot.infra.bootstrap.run_cycle",
                return_value={
                    "result": "FAILED",
                    "summary": "FAILED: execution error",
                    "run_id": "run_1",
                    "trade_id": "trade_1",
                },
            ),
            patch("apps.gmo_bot.infra.bootstrap.threading.Thread", _FakeThread),
            patch("apps.gmo_bot.infra.bootstrap._should_execute_cycle", return_value=True),
        ):
            runtime = bootstrap()
            runtime.start()
            runtime.stop()

        notifier = _StrictFakeNotifier.instances[0]
        self.assertEqual(1, len(notifier.trade_errors))
        self.assertEqual("FAILED", notifier.trade_errors[0]["result"])
        self.assertEqual(1, len(notifier.startup_payloads))
        self.assertEqual(["shutdown signal received"], notifier.shutdown_reasons)
        self.assertEqual([], notifier.consecutive_failures)

    def test_disabled_model_is_not_loaded_into_runtime(self) -> None:
        _StrictFakeNotifier.instances.clear()
        run_cycle_mock = unittest.mock.Mock(
            return_value={
                "result": "FAILED",
                "summary": "FAILED: should not run",
                "run_id": "run_1",
                "trade_id": "trade_1",
            }
        )
        with (
            patch("apps.gmo_bot.infra.bootstrap.load_env", return_value=_FakeEnv()),
            patch("apps.gmo_bot.infra.bootstrap.create_logger", return_value=_FakeLogger()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreClient", _FakeFirestoreClient),
            patch("apps.gmo_bot.infra.bootstrap.Redis.from_url", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreConfigRepository", return_value=_FakeConfigRepo(enabled=False)),
            patch("apps.gmo_bot.infra.bootstrap.GmoApiClient", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.OhlcvProvider", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.PaperExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.GmoMarginExecutionAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.FirestoreRepository", return_value=_FakePersistence()),
            patch("apps.gmo_bot.infra.bootstrap.RedisLockAdapter", return_value=object()),
            patch("apps.gmo_bot.infra.bootstrap.SlackNotifier", _StrictFakeNotifier),
            patch("apps.gmo_bot.infra.bootstrap.create_cron_cycle", return_value=_FakeCronController()),
            patch("apps.gmo_bot.infra.bootstrap.run_cycle", run_cycle_mock),
            patch("apps.gmo_bot.infra.bootstrap.threading.Thread", _FakeThread),
            patch("apps.gmo_bot.infra.bootstrap._should_execute_cycle", return_value=True),
        ):
            runtime = bootstrap()
            runtime.start()
            runtime.stop()

        notifier = _StrictFakeNotifier.instances[0]
        self.assertEqual([[]], notifier.startup_payloads)
        run_cycle_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
