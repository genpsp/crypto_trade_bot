from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from unittest.mock import Mock, patch

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient


class GmoApiClientOrderIdParsingTest(unittest.TestCase):
    def test_create_order_posts_expected_body_shape(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216832940"}) as post_mock:
            client.create_order(
                symbol="SOL_JPY",
                side="BUY",
                execution_type="LIMIT",
                size=0.1,
                price=15000.0,
                time_in_force="FAK",
            )

        post_mock.assert_called_once_with(
            "/v1/order",
            {
                "symbol": "SOL_JPY",
                "side": "BUY",
                "executionType": "LIMIT",
                "size": "0.1",
                "price": "15000",
                "timeInForce": "FAK",
            },
        )

    def test_create_order_accepts_string_order_id_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216832940"}):
            order_id = client.create_order(symbol="SOL_JPY", side="BUY", execution_type="MARKET", size=0.1)

        self.assertEqual(8216832940, order_id)

    def test_create_close_order_posts_settle_position_as_single_item_array(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        settle_position = {"positionId": 1, "size": "0.1"}
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216882125"}) as post_mock:
            client.create_close_order(
                symbol="SOL_JPY",
                side="SELL",
                execution_type="STOP",
                settle_position=settle_position,
                price=14900.0,
                time_in_force="FAK",
            )

        post_mock.assert_called_once_with(
            "/v1/closeOrder",
            {
                "symbol": "SOL_JPY",
                "side": "SELL",
                "executionType": "STOP",
                "settlePosition": [settle_position],
                "price": "14900",
                "timeInForce": "FAK",
            },
        )

    def test_create_close_order_accepts_string_order_id_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216882125"}):
            order_id = client.create_close_order(
                symbol="SOL_JPY",
                side="SELL",
                execution_type="MARKET",
                settle_position={"positionId": 1, "size": "0.1"},
            )

        self.assertEqual(8216882125, order_id)

    def test_create_close_bulk_order_posts_expected_body_shape(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216882126"}) as post_mock:
            client.create_close_bulk_order(
                symbol="SOL_JPY",
                side="SELL",
                execution_type="STOP",
                size=0.3,
                price=14900.0,
                time_in_force="FAK",
            )

        post_mock.assert_called_once_with(
            "/v1/closeBulkOrder",
            {
                "symbol": "SOL_JPY",
                "side": "SELL",
                "executionType": "STOP",
                "size": "0.3",
                "price": "14900",
                "timeInForce": "FAK",
            },
        )

    def test_create_close_bulk_order_accepts_string_order_id_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": "8216882126"}):
            order_id = client.create_close_bulk_order(
                symbol="SOL_JPY",
                side="SELL",
                execution_type="MARKET",
                size=0.3,
            )

        self.assertEqual(8216882126, order_id)

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

    def test_get_order_accepts_nested_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {
            "status": 0,
            "data": {"pagination": {"currentPage": 1, "count": 1}, "list": [{"orderId": 123, "status": "ORDERED"}]},
        }
        with patch.object(client, "private_get", return_value=payload):
            order = client.get_order(123)

        self.assertEqual({"orderId": 123, "status": "ORDERED"}, order)

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

    def test_private_put_ws_auth_signs_without_body(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"status": 0, "data": None}

        with patch("apps.gmo_bot.adapters.execution.gmo_api_client.time.time", return_value=1234.567):
            with patch.object(client.session, "request", return_value=response) as request_mock:
                client.private_put("/v1/ws-auth", {"token": "token_123"})

        headers = request_mock.call_args.kwargs["headers"]
        payload = request_mock.call_args.kwargs["data"]
        expected_timestamp = "1234567"
        expected_sign_text = expected_timestamp + "PUT" + "/v1/ws-auth"
        expected_sign = hmac.new(
            b"secret",
            expected_sign_text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(json.dumps({"token": "token_123"}, separators=(",", ":")), payload)
        self.assertEqual(expected_timestamp, headers["API-TIMESTAMP"])
        self.assertEqual(expected_sign, headers["API-SIGN"])
        self.assertEqual("application/json", headers["Content-Type"])

    def test_private_post_sets_json_content_type_header(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"status": 0, "data": "8216832940"}

        with patch.object(client.session, "request", return_value=response) as request_mock:
            client.private_post("/v1/order", {"symbol": "SOL_JPY"})

        headers = request_mock.call_args.kwargs["headers"]
        self.assertEqual("application/json", headers["Content-Type"])

    def test_cancel_order_posts_order_id(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        with patch.object(client, "private_post", return_value={"status": 0, "data": None}) as post_mock:
            client.cancel_order(12345)

        post_mock.assert_called_once_with("/v1/cancelOrder", {"orderId": 12345})

    def test_get_open_positions_accepts_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {"status": 0, "data": [{"positionId": 10, "size": "0.5", "orderdSize": "0.1"}]}
        with patch.object(client, "private_get", return_value=payload):
            positions = client.get_open_positions("SOL_JPY")

        self.assertEqual([{"positionId": 10, "size": "0.5", "orderdSize": "0.1"}], positions)

    def test_get_open_positions_accepts_nested_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {
            "status": 0,
            "data": {"pagination": {"count": 1, "page": 1}, "list": [{"positionId": 10, "size": "0.5"}]},
        }
        with patch.object(client, "private_get", return_value=payload):
            positions = client.get_open_positions("SOL_JPY")

        self.assertEqual([{"positionId": 10, "size": "0.5"}], positions)

    def test_get_open_positions_paginates_until_short_page(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        page1 = {
            "status": 0,
            "data": {"pagination": {"currentPage": 1, "count": 100}, "list": [{"positionId": index} for index in range(100)]},
        }
        page2 = {
            "status": 0,
            "data": {"pagination": {"currentPage": 2, "count": 1}, "list": [{"positionId": 100}]},
        }
        with patch.object(client, "private_get", side_effect=[page1, page2]) as get_mock:
            positions = client.get_open_positions("SOL_JPY")

        self.assertEqual(101, len(positions))
        self.assertEqual({"symbol": "SOL_JPY", "page": 1, "count": 100}, get_mock.call_args_list[0].args[1])
        self.assertEqual({"symbol": "SOL_JPY", "page": 2, "count": 100}, get_mock.call_args_list[1].args[1])

    def test_get_active_orders_accepts_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {"status": 0, "data": [{"orderId": 123, "settleType": "CLOSE"}]}
        with patch.object(client, "private_get", return_value=payload):
            orders = client.get_active_orders("SOL_JPY")

        self.assertEqual([{"orderId": 123, "settleType": "CLOSE"}], orders)

    def test_get_active_orders_accepts_nested_list_payload(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        payload = {
            "status": 0,
            "data": {"pagination": {"count": 1, "page": 1}, "list": [{"orderId": 123, "settleType": "CLOSE"}]},
        }
        with patch.object(client, "private_get", return_value=payload):
            orders = client.get_active_orders("SOL_JPY")

        self.assertEqual([{"orderId": 123, "settleType": "CLOSE"}], orders)

    def test_get_active_orders_paginates_until_short_page(self) -> None:
        client = GmoApiClient(api_key="key", api_secret="secret")
        page1 = {
            "status": 0,
            "data": {"pagination": {"currentPage": 1, "count": 100}, "list": [{"orderId": index} for index in range(100)]},
        }
        page2 = {
            "status": 0,
            "data": {"pagination": {"currentPage": 2, "count": 1}, "list": [{"orderId": 100}]},
        }
        with patch.object(client, "private_get", side_effect=[page1, page2]) as get_mock:
            orders = client.get_active_orders("SOL_JPY")

        self.assertEqual(101, len(orders))
        self.assertEqual({"symbol": "SOL_JPY", "page": 1, "count": 100}, get_mock.call_args_list[0].args[1])
        self.assertEqual({"symbol": "SOL_JPY", "page": 2, "count": 100}, get_mock.call_args_list[1].args[1])


if __name__ == "__main__":
    unittest.main()
