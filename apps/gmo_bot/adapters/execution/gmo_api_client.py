from __future__ import annotations

import hashlib
import hmac
import json
import random
import threading
import time
from typing import Any

import requests

from apps.gmo_bot.domain.utils.numeric import decimal_str as _decimal_str

PUBLIC_API_BASE_URL = "https://api.coin.z.com/public"
PRIVATE_API_BASE_URL = "https://api.coin.z.com/private"
DEFAULT_HTTP_TIMEOUT_SECONDS = 10
# Retry policy for transient failures (idempotent calls only).
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 0.3
RETRY_BACKOFF_MAX_SECONDS = 2.0
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


class GmoApiClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        public_base_url: str = PUBLIC_API_BASE_URL,
        private_base_url: str = PRIVATE_API_BASE_URL,
        timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.public_base_url = public_base_url.rstrip("/")
        self.private_base_url = private_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # requests.Session is not documented as thread-safe: connection pool
        # corruption and header bleed have been observed under concurrent use.
        # Give each thread its own Session.
        self._session_local = threading.local()

    @property
    def session(self) -> requests.Session:
        existing = getattr(self._session_local, "value", None)
        if existing is None:
            existing = requests.Session()
            self._session_local.value = existing
        return existing

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", self.public_base_url, path, params=params, retry=True)

    def private_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", self.private_base_url, path, params=params, private=True, retry=True)

    def private_post(self, path: str, body: dict[str, Any], *, retry: bool = False) -> Any:
        return self._request("POST", self.private_base_url, path, body=body, private=True, retry=retry)

    def private_put(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("PUT", self.private_base_url, path, body=body, private=True)

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        payload = self.public_get("/v1/ticker", {"symbol": symbol})
        data = payload.get("data")
        if isinstance(data, list) and data:
            value = data[0]
            if isinstance(value, dict):
                return value
        raise RuntimeError(f"GMO ticker payload invalid for {symbol}")

    def get_klines(self, symbol: str, interval: str, date: str) -> list[dict[str, Any]]:
        payload = self.public_get(
            "/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "date": date,
            },
        )
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        raise RuntimeError(f"GMO klines payload invalid for {symbol} {interval} {date}")

    def get_symbols(self) -> list[dict[str, Any]]:
        payload = self.public_get("/v1/symbols")
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        raise RuntimeError("GMO symbols payload invalid")

    def get_margin(self) -> dict[str, Any]:
        payload = self.private_get("/v1/account/margin")
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        raise RuntimeError("GMO margin payload invalid")

    def create_ws_access_token(self) -> str:
        payload = self.private_post("/v1/ws-auth", {})
        data = payload.get("data")
        if isinstance(data, str) and data.strip():
            return data
        raise RuntimeError("GMO /v1/ws-auth payload invalid")

    def extend_ws_access_token(self, token: str) -> None:
        payload = self.private_put("/v1/ws-auth", {"token": token})
        data = payload.get("data")
        if isinstance(data, str) and data == token:
            return
        if data is None:
            return
        raise RuntimeError("GMO /v1/ws-auth refresh payload invalid")

    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        execution_type: str,
        size: float,
        price: float | None = None,
        time_in_force: str | None = None,
    ) -> int:
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "executionType": execution_type,
            "size": _decimal_str(size),
        }
        if price is not None:
            body["price"] = _decimal_str(price)
        if time_in_force is not None:
            body["timeInForce"] = time_in_force
        payload = self.private_post("/v1/order", body)
        return _extract_order_id(payload, path="/v1/order")

    def create_close_order(
        self,
        *,
        symbol: str,
        side: str,
        execution_type: str,
        settle_position: dict[str, Any],
        price: float | None = None,
        time_in_force: str | None = None,
    ) -> int:
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "executionType": execution_type,
            "settlePosition": [settle_position],
        }
        if price is not None:
            body["price"] = _decimal_str(price)
        if time_in_force is not None:
            body["timeInForce"] = time_in_force
        payload = self.private_post("/v1/closeOrder", body)
        return _extract_order_id(payload, path="/v1/closeOrder")

    def create_close_bulk_order(
        self,
        *,
        symbol: str,
        side: str,
        execution_type: str,
        size: float,
        price: float | None = None,
        time_in_force: str | None = None,
    ) -> int:
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "executionType": execution_type,
            "size": _decimal_str(size),
        }
        if price is not None:
            body["price"] = _decimal_str(price)
        if time_in_force is not None:
            body["timeInForce"] = time_in_force
        payload = self.private_post("/v1/closeBulkOrder", body)
        return _extract_order_id(payload, path="/v1/closeBulkOrder")

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        payload = self.private_get("/v1/orders", {"orderId": order_id})
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and int(item.get("orderId", 0)) == order_id:
                    return item
            return None
        if isinstance(data, dict):
            nested_list = data.get("list")
            if isinstance(nested_list, list):
                for item in nested_list:
                    if isinstance(item, dict) and int(item.get("orderId", 0)) == order_id:
                        return item
                return None
        raise RuntimeError(f"GMO orders payload invalid for order_id={order_id}")

    def get_executions(self, order_id: int) -> list[dict[str, Any]]:
        payload = self.private_get("/v1/executions", {"orderId": order_id})
        data = payload.get("data")
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            nested_list = data.get("list")
            if nested_list is None:
                return []
            if isinstance(nested_list, list):
                return [item for item in nested_list if isinstance(item, dict)]
        raise RuntimeError(f"GMO executions payload invalid for order_id={order_id}")

    def get_open_positions(self, symbol: str) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.private_get("/v1/openPositions", {"symbol": symbol, "page": page, "count": 100})
            data = payload.get("data")
            if isinstance(data, list):
                if page > 1:
                    raise RuntimeError(f"GMO openPositions payload invalid for symbol={symbol}")
                return [item for item in data if isinstance(item, dict)]
            if not isinstance(data, dict):
                raise RuntimeError(f"GMO openPositions payload invalid for symbol={symbol}")
            nested_list = data.get("list")
            if not isinstance(nested_list, list):
                raise RuntimeError(f"GMO openPositions payload invalid for symbol={symbol}")
            page_items = [item for item in nested_list if isinstance(item, dict)]
            positions.extend(page_items)
            if len(nested_list) < 100:
                return positions
            page += 1

    def get_active_orders(self, symbol: str) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.private_get("/v1/activeOrders", {"symbol": symbol, "page": page, "count": 100})
            data = payload.get("data")
            if isinstance(data, list):
                if page > 1:
                    raise RuntimeError(f"GMO activeOrders payload invalid for symbol={symbol}")
                return [item for item in data if isinstance(item, dict)]
            if not isinstance(data, dict):
                raise RuntimeError(f"GMO activeOrders payload invalid for symbol={symbol}")
            nested_list = data.get("list")
            if not isinstance(nested_list, list):
                raise RuntimeError(f"GMO activeOrders payload invalid for symbol={symbol}")
            page_items = [item for item in nested_list if isinstance(item, dict)]
            orders.extend(page_items)
            if len(nested_list) < 100:
                return orders
            page += 1

    def cancel_order(self, order_id: int) -> None:
        # cancelOrder is idempotent at the GMO API level (re-cancel of a finished
        # order returns an application error, but transport-level retries are safe).
        self.private_post("/v1/cancelOrder", {"orderId": order_id}, retry=True)

    def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        private: bool = False,
        retry: bool = False,
    ) -> Any:
        attempts = RETRY_MAX_ATTEMPTS if retry else 1
        last_error: Exception | None = None
        for attempt_index in range(attempts):
            try:
                return self._send_request(
                    method=method,
                    base_url=base_url,
                    path=path,
                    params=params,
                    body=body,
                    private=private,
                )
            except (requests.ConnectionError, requests.Timeout) as error:
                last_error = error
            except requests.HTTPError as error:
                status_code = getattr(getattr(error, "response", None), "status_code", None)
                if status_code not in RETRYABLE_HTTP_STATUSES:
                    raise
                last_error = error
            if attempt_index >= attempts - 1:
                break
            # Exponential backoff with full jitter to avoid thundering herd across
            # concurrent model cycles hitting the same rate-limited endpoint.
            base_delay = min(
                RETRY_BACKOFF_MAX_SECONDS,
                RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt_index),
            )
            time.sleep(random.uniform(0, base_delay))
        assert last_error is not None
        raise last_error

    def _send_request(
        self,
        *,
        method: str,
        base_url: str,
        path: str,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        private: bool,
    ) -> Any:
        url = f"{base_url}{path}"
        headers: dict[str, str] = {}
        payload: str | None = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"
        if private:
            timestamp = str(int(time.time() * 1000))
            text = timestamp + method.upper() + path
            if payload is not None and _should_sign_private_body(method.upper(), path):
                text += payload
            sign = hmac.new(self.api_secret, text.encode("utf-8"), hashlib.sha256).hexdigest()
            headers.update(
                {
                    "API-KEY": self.api_key,
                    "API-TIMESTAMP": timestamp,
                    "API-SIGN": sign,
                }
            )
        response = self.session.request(
            method.upper(),
            url,
            params=params,
            data=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload_json = response.json()
        if not isinstance(payload_json, dict):
            raise RuntimeError(f"GMO API returned non-object payload: {payload_json}")
        status = payload_json.get("status")
        if status != 0:
            raise RuntimeError(_build_gmo_error_message(payload_json))
        return payload_json


def _build_gmo_error_message(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        parts: list[str] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            code = item.get("message_code")
            text = item.get("message_string")
            if code and text:
                parts.append(f"{code}: {text}")
            elif text:
                parts.append(str(text))
        if parts:
            return f"GMO API error status={payload.get('status')}: {' | '.join(parts)}"
    return f"GMO API error status={payload.get('status')}: {payload}"


def _should_sign_private_body(method: str, path: str) -> bool:
    # GMO's ws-auth PUT/DELETE examples sign only timestamp + method + path.
    return not (path == "/v1/ws-auth" and method in {"PUT", "DELETE"})


def _extract_order_id(payload: dict[str, Any], *, path: str) -> int:
    data = payload.get("data")
    if isinstance(data, int):
        return data
    if isinstance(data, str):
        try:
            return int(data)
        except ValueError as error:
            raise RuntimeError(f"GMO {path} payload invalid: {payload}") from error
    if isinstance(data, dict):
        raw_order_id = data.get("orderId")
        if isinstance(raw_order_id, int):
            return raw_order_id
        if isinstance(raw_order_id, str):
            try:
                return int(raw_order_id)
            except ValueError as error:
                raise RuntimeError(f"GMO {path} payload invalid: {payload}") from error
    raise RuntimeError(f"GMO {path} payload invalid: {payload}")
