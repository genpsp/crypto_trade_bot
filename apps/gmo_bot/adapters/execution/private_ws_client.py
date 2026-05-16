from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
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
# 7.1: emit an error log when the WS stays unsubscribed past this threshold so
# the fallback poll loop is at least visible in Cloud Logging.
SUBSCRIBE_STALE_THRESHOLD_SECONDS = 5 * 60


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
        # Track when we last successfully completed `subscribe` to detect
        # silent reconnect loops where the socket repeatedly fails to subscribe.
        self._last_subscribed_at: datetime | None = None
        self._last_stale_alert_at: datetime | None = None

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
            self._maybe_alert_stale_subscription()
            if not self._stop_event.wait(RECONNECT_DELAY_SECONDS):
                continue
            break

    def _maybe_alert_stale_subscription(self) -> None:
        now = datetime.now(tz=UTC)
        last_subscribed = self._last_subscribed_at
        elapsed = (
            (now - last_subscribed).total_seconds()
            if last_subscribed is not None
            else SUBSCRIBE_STALE_THRESHOLD_SECONDS + 1
        )
        if elapsed < SUBSCRIBE_STALE_THRESHOLD_SECONDS:
            return
        last_alert = self._last_stale_alert_at
        if last_alert is not None and (now - last_alert).total_seconds() < SUBSCRIBE_STALE_THRESHOLD_SECONDS:
            return
        self.logger.error(
            "gmo private websocket has not subscribed within threshold; falling back to polling exits",
            {"elapsed_seconds": int(elapsed), "threshold_seconds": SUBSCRIBE_STALE_THRESHOLD_SECONDS},
        )
        self._last_stale_alert_at = now

    def _token_refresh_loop(self) -> None:
        while not self._stop_event.wait(TOKEN_REFRESH_SECONDS):
            with self._token_lock:
                token = self._current_token
            if not token:
                continue
            try:
                self.client.extend_ws_access_token(token)
            except Exception as error:
                # 7.3: token extension failed; rather than waiting for the token
                # to expire mid-cycle (and lose TP/SL events), close the socket
                # immediately so _socket_loop reconnects with a fresh token.
                self.logger.warn(
                    "gmo private websocket token refresh failed; forcing reconnect",
                    {"error": str(error)},
                )
                app = self._app
                if app is not None:
                    try:
                        app.close()
                    except Exception:
                        pass

    def _on_open(self, ws_app: Any) -> None:
        for index, channel in enumerate(self.channels):
            if index > 0:
                time.sleep(SUBSCRIBE_INTERVAL_SECONDS)
            ws_app.send(json.dumps({"command": "subscribe", "channel": channel}))
        self.logger.info("gmo private websocket subscribed", {"channels": list(self.channels)})
        self._last_subscribed_at = datetime.now(tz=UTC)
        self._last_stale_alert_at = None

    def _on_message(self, ws_app: Any, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.logger.warn("gmo private websocket delivered invalid json", {"message": message})
            return
        if not isinstance(payload, dict):
            return
        if payload.get("error") is not None:
            # 7.2: subscribe-time errors should trigger a reconnect instead of
            # silently leaving the socket unsubscribed.
            self.logger.warn("gmo private websocket event error", {"payload": payload})
            error_text = str(payload.get("error") or "").lower()
            if "subscribe" in error_text or "auth" in error_text or "token" in error_text:
                try:
                    ws_app.close()
                except Exception:
                    pass
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
