from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.app.ports.logger_port import LoggerPort

try:
    import websocket  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised in runtime environments without the extra package.
    websocket = None


PRIVATE_WS_URL_BASE = "wss://api.coin.z.com/ws/private/v1"
DEFAULT_CHANNELS = ("orderEvents", "executionEvents")
TOKEN_REFRESH_SECONDS = 45 * 60
RECONNECT_DELAY_SECONDS = 3.0
SUBSCRIBE_INTERVAL_SECONDS = 1.1


class GmoPrivateWebSocketClient:
    def __init__(
        self,
        *,
        client: GmoApiClient,
        logger: LoggerPort,
        on_event: Callable[[dict[str, Any]], None],
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
    ):
        self.client = client
        self.logger = logger
        self.on_event = on_event
        self.channels = channels
        self._stop_event = threading.Event()
        self._socket_thread: threading.Thread | None = None
        self._token_thread: threading.Thread | None = None
        self._token_lock = threading.Lock()
        self._current_token: str | None = None
        self._app: Any | None = None

    def start(self) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client package is required for GMO private websocket monitoring")
        if self._socket_thread is not None:
            return
        self._stop_event.clear()
        self._socket_thread = threading.Thread(target=self._socket_loop, name="gmo-private-ws", daemon=True)
        self._token_thread = threading.Thread(target=self._token_refresh_loop, name="gmo-private-ws-token", daemon=True)
        self._socket_thread.start()
        self._token_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        if self._socket_thread is not None:
            self._socket_thread.join(timeout=5)
            self._socket_thread = None
        if self._token_thread is not None:
            self._token_thread.join(timeout=5)
            self._token_thread = None

    def _socket_loop(self) -> None:
        assert websocket is not None
        while not self._stop_event.is_set():
            try:
                token = self.client.create_ws_access_token()
                with self._token_lock:
                    self._current_token = token
                ws_url = f"{PRIVATE_WS_URL_BASE}/{token}"
                app = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._app = app
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as error:
                self.logger.warn("gmo private websocket loop error", {"error": str(error)})
            finally:
                self._app = None
            if not self._stop_event.wait(RECONNECT_DELAY_SECONDS):
                continue
            break

    def _token_refresh_loop(self) -> None:
        while not self._stop_event.wait(TOKEN_REFRESH_SECONDS):
            with self._token_lock:
                token = self._current_token
            if not token:
                continue
            try:
                self.client.extend_ws_access_token(token)
            except Exception as error:
                self.logger.warn("gmo private websocket token refresh failed", {"error": str(error)})

    def _on_open(self, ws_app: Any) -> None:
        for index, channel in enumerate(self.channels):
            if index > 0:
                time.sleep(SUBSCRIBE_INTERVAL_SECONDS)
            ws_app.send(json.dumps({"command": "subscribe", "channel": channel}))
        self.logger.info("gmo private websocket subscribed", {"channels": list(self.channels)})

    def _on_message(self, _ws_app: Any, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.logger.warn("gmo private websocket delivered invalid json", {"message": message})
            return
        if not isinstance(payload, dict):
            return
        if payload.get("error") is not None:
            self.logger.warn("gmo private websocket event error", {"payload": payload})
            return
        self.on_event(payload)

    def _on_error(self, _ws_app: Any, error: Any) -> None:
        if self._stop_event.is_set():
            return
        self.logger.warn("gmo private websocket error", {"error": str(error)})

    def _on_close(self, _ws_app: Any, status_code: Any, message: Any) -> None:
        if self._stop_event.is_set():
            return
        self.logger.warn(
            "gmo private websocket closed",
            {"status_code": status_code, "message": message},
        )
