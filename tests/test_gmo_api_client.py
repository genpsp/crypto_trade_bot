from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient


class GmoApiClientOrderIdParsingTest(unittest.TestCase):
    def test_create_order_accepts_string_order_id_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216832940"}):
            order_id = client.create_order(symbol="SOL_JPY", side="BUY", execution_type="MARKET", size=0.1)

        self.assertEqual(8216832940, order_id)

    def test_create_close_order_accepts_string_order_id_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216882125"}):
            order_id = client.create_close_order(
                symbol="SOL_JPY",
                side="SELL",
                execution_type="MARKET",
                settle_positions=[{"positionId": 1, "size": "0.1"}],
            )

        self.assertEqual(8216882125, order_id)

    def test_get_executions_accepts_bare_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {"status": 0, "data": [{"executionId": 1}, {"executionId": 2}]}
        with patch.object(client, "private_get", return_value=payload):
            executions = client.get_executions(8216882515)

        self.assertEqual([{"executionId": 1}, {"executionId": 2}], executions)

    def test_get_executions_accepts_nested_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {"status": 0, "data": {"list": [{"executionId": 1530581068, "side": "SELL"}]}}
        with patch.object(client, "private_get", return_value=payload):
            executions = client.get_executions(8216882515)

        self.assertEqual([{"executionId": 1530581068, "side": "SELL"}], executions)

    def test_create_ws_access_token_accepts_string_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "token_123"}):
            token = client.create_ws_access_token()

        self.assertEqual("token_123", token)

    def test_extend_ws_access_token_accepts_null_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_put", return_value={"status": 0, "data": None}) as put_mock:
            client.extend_ws_access_token("token_123")

        put_mock.assert_called_once_with("/v1/ws-auth", {"token": "token_123"})

    def test_cancel_order_posts_order_id(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": None}) as post_mock:
            client.cancel_order(12345)

        post_mock.assert_called_once_with("/v1/cancelOrder", {"orderId": 12345})


if __name__ == "__main__":
    unittest.main()
