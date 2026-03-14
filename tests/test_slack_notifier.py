from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from apps.dex_bot.infra.alerting.daily_trade_summary import build_daily_summary_report
from apps.dex_bot.infra.alerting.slack_notifier import SlackAlertConfig, SlackNotifier, is_execution_error_result
from apps.gmo_bot.infra.alerting.daily_trade_summary import (
    build_daily_trade_summary_report as build_gmo_daily_trade_summary_report,
)
from apps.gmo_bot.infra.alerting.slack_notifier import (
    is_execution_error_result as is_gmo_execution_error_result,
    is_market_data_maintenance_result,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object] | None]] = []

    def info(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, object] | None = None) -> None:
        self.warnings.append((message, context))

    def error(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context


class _FakeDedupeStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def set(self, key: str, _value: str, *, ex: int, nx: bool) -> bool:
        _ = ex
        if nx and key in self._keys:
            return False
        self._keys.add(key)
        return True


class SlackNotifierTest(unittest.TestCase):
    def test_is_execution_error_result_handles_failed_and_skipped_markers(self) -> None:
        self.assertTrue(is_execution_error_result("FAILED", "FAILED: any error"))
        self.assertTrue(
            is_execution_error_result(
                "SKIPPED",
                "SKIPPED: insufficient funds (Transaction simulation failed)",
            )
        )
        self.assertFalse(is_execution_error_result("NO_SIGNAL", "NO_SIGNAL: trend filter failed"))
        self.assertFalse(is_execution_error_result("SKIPPED", "SKIPPED: max_trades_per_day reached"))

    def test_notify_trade_error_is_deduplicated(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(
                webhook_url="https://hooks.slack.com/services/test/test/test",
                duplicate_suppression_seconds=300,
            ),
            logger=logger,
        )

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_trade_error(
                model_id="ema_pullback_15m_both_v0",
                result="FAILED",
                summary="FAILED: sendTransaction failed",
                run_id="run_1",
                trade_id="trade_1",
            )
            notifier.notify_trade_error(
                model_id="ema_pullback_15m_both_v0",
                result="FAILED",
                summary="FAILED: sendTransaction failed",
                run_id="run_2",
                trade_id="trade_1",
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("売買実行エラー", payload)
        self.assertIn("```", payload)
        self.assertIn("model=ema_pullback_15m_both_v0", payload)
        self.assertEqual(0, len(logger.warnings))

    def test_notify_trade_closed_formats_take_profit_notification(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(
                webhook_url="https://hooks.slack.com/services/test/test/test",
                duplicate_suppression_seconds=300,
            ),
            logger=logger,
        )

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_trade_closed(
                model_id="ema_pullback_15m_both_v0",
                trade_id="trade_1",
                pair="SOL/USDC",
                direction="LONG",
                close_reason="TAKE_PROFIT",
                entry_price=100.0,
                exit_price=102.5,
                gross_pnl=2.5,
                fee=0.023,
                net_pnl=2.477,
                quote_ccy="USDC",
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("利確確定", payload)
        self.assertIn("trade_id=trade_1", payload)
        self.assertIn("reason=TAKE_PROFIT", payload)
        self.assertIn("gross_pnl_usdc=2.5000", payload)
        self.assertIn("fee_usdc=0.0230", payload)
        self.assertIn("net_pnl_usdc=2.4770", payload)

    def test_gmo_maintenance_is_not_classified_as_execution_error(self) -> None:
        reason = "GMO API error status=5: ERR-5201: MAINTENANCE. Please wait for a while"
        self.assertTrue(
            is_market_data_maintenance_result(
                "FAILED",
                "FAILED: unhandled run_cycle error",
                reason,
            )
        )
        self.assertFalse(
            is_gmo_execution_error_result(
                "FAILED",
                "FAILED: unhandled run_cycle error",
                reason,
            )
        )
        self.assertFalse(
            is_gmo_execution_error_result(
                "SKIPPED",
                "SKIPPED: market data unavailable (maintenance)",
                reason,
            )
        )

    def test_notify_runtime_config_error_is_deduplicated(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(
                webhook_url="https://hooks.slack.com/services/test/test/test",
                duplicate_suppression_seconds=300,
            ),
            logger=logger,
        )

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_runtime_config_error(
                model_id="ema_pullback_15m_both_v0",
                context="failed_to_load_model_config",
                error="models/ema_pullback_15m_both_v0.direction must be LONG, SHORT or BOTH",
            )
            notifier.notify_runtime_config_error(
                model_id="ema_pullback_15m_both_v0",
                context="failed_to_load_model_config",
                error="models/ema_pullback_15m_both_v0.direction must be LONG, SHORT or BOTH",
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("実行設定エラー", payload)
        self.assertIn("model=ema_pullback_15m_both_v0", payload)
        self.assertIn("context=failed_to_load_model_config", payload)

    def test_notify_startup_formats_message_in_japanese_code_block(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
            logger=logger,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_startup(
                [
                    {
                        "model_id": "ema_pullback_15m_both_v0",
                        "mode": "LIVE",
                        "strategy": "ema_trend_pullback_15m_v0",
                    }
                ]
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("Bot起動", payload)
        self.assertIn("```", payload)
        self.assertIn("ema_pullback_15m_both_v0", payload)

    def test_notify_startup_is_deduplicated_across_notifier_restarts_when_shared_store_is_available(self) -> None:
        logger = _FakeLogger()
        dedupe_store = _FakeDedupeStore()
        response = Mock()
        response.raise_for_status.return_value = None

        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            first = SlackNotifier(
                config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
                logger=logger,
                dedupe_store=dedupe_store,
                dedupe_namespace="dex_bot",
            )
            second = SlackNotifier(
                config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
                logger=logger,
                dedupe_store=dedupe_store,
                dedupe_namespace="dex_bot",
            )
            payload = [{"model_id": "ema_pullback_15m_both_v0", "mode": "LIVE", "strategy": "ema_trend_pullback_15m_v0"}]
            first.notify_startup(payload)
            second.notify_startup(payload)

        self.assertEqual(1, mocked_post.call_count)

    def test_disabled_notifier_does_not_post(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url=None),
            logger=logger,
        )

        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post") as mocked_post:
            notifier.notify_shutdown(reason="test stop")
        self.assertEqual(0, mocked_post.call_count)

    def test_notify_daily_trade_summary_jst_formats_decorated_table(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
            logger=logger,
        )
        report = build_daily_summary_report(
            target_date_jst="2026-02-26",
            generated_at_utc=datetime(2026, 2, 26, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "t1",
                            "direction": "LONG",
                            "state": "CLOSED",
                            "created_at": "2026-02-25T15:10:00Z",
                            "position": {
                                "quote_amount_usdc": 100.0,
                                "quantity_sol": 1.0,
                                "entry_trigger_price": 100.0,
                                "entry_price": 100.0,
                                "exit_trigger_price": 101.0,
                                "exit_price": 102.0,
                                "exit_time_iso": "2026-02-25T18:00:00Z",
                            },
                            "execution": {"exit_result": {"spent_quote_usdc": 102.0}},
                        }
                    ],
                    [{"result": "NO_SIGNAL", "executed_at_iso": "2026-02-25T16:00:00Z"}],
                )
            ],
        )

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_daily_trade_summary_jst(report=report)

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("【日次トレード結果サマリ（JST）】", payload)
        self.assertIn("TOTAL", payload)
        self.assertIn("ema_pullback_15m_both_v0", payload)
        self.assertIn("```", payload)

    def test_notify_combined_daily_trade_summary_jst_formats_dex_and_gmo_sections(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
            logger=logger,
        )
        dex_report = build_daily_summary_report(
            target_date_jst="2026-02-26",
            generated_at_utc=datetime(2026, 2, 26, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "dex_t1",
                            "direction": "LONG",
                            "state": "CLOSED",
                            "created_at": "2026-02-25T15:10:00Z",
                            "position": {
                                "quote_amount_usdc": 100.0,
                                "quantity_sol": 1.0,
                                "entry_trigger_price": 100.0,
                                "entry_price": 100.0,
                                "exit_trigger_price": 101.0,
                                "exit_price": 102.0,
                                "exit_time_iso": "2026-02-25T18:00:00Z",
                            },
                            "execution": {"exit_result": {"spent_quote_usdc": 102.0}},
                        }
                    ],
                    [{"result": "NO_SIGNAL", "executed_at_iso": "2026-02-25T16:00:00Z"}],
                )
            ],
        )
        gmo_report = build_gmo_daily_trade_summary_report(
            target_date_jst="2026-02-26",
            generated_at_utc=datetime(2026, 2, 26, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "gmo_ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "gmo_t1",
                            "direction": "SHORT",
                            "state": "CLOSED",
                            "created_at": "2026-02-25T15:20:00Z",
                            "position": {
                                "quote_amount_jpy": 6500.0,
                                "quantity_sol": 0.5,
                                "entry_trigger_price": 13000.0,
                                "entry_price": 13000.0,
                                "exit_trigger_price": 12900.0,
                                "exit_price": 12800.0,
                                "exit_time_iso": "2026-02-25T18:20:00Z",
                            },
                            "execution": {
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 3.0,
                                "exit_result": {"filled_quote_jpy": 6400.0},
                            },
                        }
                    ],
                    [{"result": "SKIPPED_ENTRY", "executed_at_iso": "2026-02-25T17:00:00Z"}],
                )
            ],
        )

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("apps.dex_bot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_combined_daily_trade_summary_jst(
                dex_report=dex_report,
                gmo_report=gmo_report,
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("【日次トレード結果サマリ（JST）】", payload)
        self.assertIn("*DEX*", payload)
        self.assertIn("*GMO*", payload)
        self.assertIn("pnl_usdc", payload)
        self.assertIn("pnl_jpy", payload)
        self.assertIn("ema_pullback_15m_both_v0", payload)
        self.assertIn("gmo_ema_pullback_15m_both_v0", payload)
        self.assertIn("```", payload)


if __name__ == "__main__":
    unittest.main()
