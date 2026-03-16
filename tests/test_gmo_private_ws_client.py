from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from apps.gmo_bot.adapters.execution.private_ws_client import GmoPrivateWebSocketClient


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


class _FakeWsApp:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send(self, payload: str) -> None:
        self.sent_messages.append(json.loads(payload))


class GmoPrivateWebSocketClientTest(unittest.TestCase):
    def test_on_open_subscribes_with_throttle_between_channels(self) -> None:
        client = GmoPrivateWebSocketClient(
            client=object(),  # unused
            logger=_FakeLogger(),
            on_event=lambda payload: payload,
        )
        ws_app = _FakeWsApp()

        with patch("apps.gmo_bot.adapters.execution.private_ws_client.time.sleep") as sleep_mock:
            client._on_open(ws_app)  # noqa: SLF001

        self.assertEqual(
            [
                {"command": "subscribe", "channel": "orderEvents"},
                {"command": "subscribe", "channel": "executionEvents"},
            ],
            ws_app.sent_messages,
        )
        sleep_mock.assert_called_once()

    def test_on_open_does_not_sleep_for_single_channel(self) -> None:
        client = GmoPrivateWebSocketClient(
            client=object(),  # unused
            logger=_FakeLogger(),
            on_event=lambda payload: payload,
            channels=("orderEvents",),
        )
        ws_app = _FakeWsApp()

        with patch("apps.gmo_bot.adapters.execution.private_ws_client.time.sleep") as sleep_mock:
            client._on_open(ws_app)  # noqa: SLF001

        self.assertEqual([{"command": "subscribe", "channel": "orderEvents"}], ws_app.sent_messages)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
