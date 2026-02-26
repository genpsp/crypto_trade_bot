from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from pybot.infra.alerting.slack_notifier import SlackAlertConfig, SlackNotifier, is_execution_error_result


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
        with patch("pybot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_trade_error(
                model_id="core_long_15m_v0",
                result="FAILED",
                summary="FAILED: sendTransaction failed",
                run_id="run_1",
                trade_id="trade_1",
            )
            notifier.notify_trade_error(
                model_id="core_long_15m_v0",
                result="FAILED",
                summary="FAILED: sendTransaction failed",
                run_id="run_2",
                trade_id="trade_1",
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("売買実行エラー", payload)
        self.assertIn("```", payload)
        self.assertIn("model=core_long_15m_v0", payload)
        self.assertEqual(0, len(logger.warnings))

    def test_notify_startup_formats_message_in_japanese_code_block(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url="https://hooks.slack.com/services/test/test/test"),
            logger=logger,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("pybot.infra.alerting.slack_notifier.requests.post", return_value=response) as mocked_post:
            notifier.notify_startup(
                [
                    {
                        "model_id": "core_long_15m_v0",
                        "mode": "LIVE",
                        "strategy": "ema_trend_pullback_15m_v0",
                    }
                ]
            )

        self.assertEqual(1, mocked_post.call_count)
        payload = mocked_post.call_args.kwargs["json"]["text"]  # type: ignore[index]
        self.assertIn("Bot起動", payload)
        self.assertIn("```", payload)
        self.assertIn("core_long_15m_v0", payload)

    def test_disabled_notifier_does_not_post(self) -> None:
        logger = _FakeLogger()
        notifier = SlackNotifier(
            config=SlackAlertConfig(webhook_url=None),
            logger=logger,
        )

        with patch("pybot.infra.alerting.slack_notifier.requests.post") as mocked_post:
            notifier.notify_shutdown(reason="test stop")
        self.assertEqual(0, mocked_post.call_count)


if __name__ == "__main__":
    unittest.main()
