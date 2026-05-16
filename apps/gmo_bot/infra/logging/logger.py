from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from apps.gmo_bot.app.ports.logger_port import LoggerPort

# 8.1: emit structured JSON when running under Cloud Logging / Cloud Run so
# severity, message, and context all map onto recognised JSON payload fields.
# Override with LOG_FORMAT=text for human-friendly local development.
_LOG_FORMAT_ENV = "LOG_FORMAT"
_TEXT_LOG_FORMAT = "text"
_JSON_LOG_FORMAT = "json"
_LOG_LEVEL_ENV = "LOG_LEVEL"
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def _now_iso_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_text_context(context: dict[str, Any] | None = None) -> str:
    if context is None or len(context) == 0:
        return ""
    return f" {json.dumps(context, ensure_ascii=False)}"


def _resolve_format() -> str:
    raw = os.environ.get(_LOG_FORMAT_ENV, "").strip().lower()
    return _TEXT_LOG_FORMAT if raw == _TEXT_LOG_FORMAT else _JSON_LOG_FORMAT


def _resolve_min_level() -> int:
    raw = os.environ.get(_LOG_LEVEL_ENV, "INFO").strip().upper()
    return _LEVEL_ORDER.get(raw, _LEVEL_ORDER["INFO"])


class ConsoleLogger(LoggerPort):
    def __init__(self, component: str = "bot", log_format: str | None = None):
        self.component = component
        self.log_format = (log_format or _resolve_format()).lower()
        self._min_level = _resolve_min_level()

    def _emit(self, severity: str, message: str, context: dict[str, Any] | None) -> None:
        if _LEVEL_ORDER.get(severity, _LEVEL_ORDER["INFO"]) < self._min_level:
            return
        if self.log_format == _TEXT_LOG_FORMAT:
            tag = severity.upper()
            print(
                f"{_now_iso_utc()} [{tag}] [{self.component}] {message}{_format_text_context(context)}",
                flush=True,
            )
            return
        # Cloud Logging recognises top-level ``severity`` and ``message`` keys.
        payload: dict[str, Any] = {
            "severity": severity,
            "message": message,
            "component": self.component,
            "timestamp": _now_iso_utc(),
        }
        if context:
            payload["context"] = context
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def debug(self, message: str, context: dict[str, Any] | None = None) -> None:
        self._emit("DEBUG", message, context)

    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        self._emit("INFO", message, context)

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        self._emit("WARNING", message, context)

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        self._emit("ERROR", message, context)


def create_logger(component: str = "bot") -> LoggerPort:
    return ConsoleLogger(component=component)
