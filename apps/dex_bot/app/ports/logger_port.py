from __future__ import annotations

from typing import Any, Protocol


class LoggerPort(Protocol):
    def info(self, message: str, context: dict[str, Any] | None = None) -> None: ...

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None: ...

    def error(self, message: str, context: dict[str, Any] | None = None) -> None: ...

