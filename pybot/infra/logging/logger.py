from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pybot.app.ports.logger_port import LoggerPort


def _now_iso_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_context(context: dict[str, Any] | None = None) -> str:
    if context is None or len(context) == 0:
        return ""
    return f" {json.dumps(context, ensure_ascii=False)}"


class ConsoleLogger(LoggerPort):
    def __init__(self, component: str = "bot"):
        self.component = component

    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"{_now_iso_utc()} [INFO] [{self.component}] {message}{_format_context(context)}", flush=True)

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"{_now_iso_utc()} [WARN] [{self.component}] {message}{_format_context(context)}", flush=True)

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"{_now_iso_utc()} [ERROR] [{self.component}] {message}{_format_context(context)}", flush=True)


def create_logger(component: str = "bot") -> LoggerPort:
    return ConsoleLogger(component=component)
