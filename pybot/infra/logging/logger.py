from __future__ import annotations

import json
from typing import Any

from pybot.app.ports.logger_port import LoggerPort


def _format_context(context: dict[str, Any] | None = None) -> str:
    if context is None or len(context) == 0:
        return ""
    return f" {json.dumps(context, ensure_ascii=False)}"


class ConsoleLogger(LoggerPort):
    def __init__(self, component: str = "bot"):
        self.component = component

    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[INFO] [{self.component}] {message}{_format_context(context)}")

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[WARN] [{self.component}] {message}{_format_context(context)}")

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[ERROR] [{self.component}] {message}{_format_context(context)}")


def create_logger(component: str = "bot") -> LoggerPort:
    return ConsoleLogger(component=component)

