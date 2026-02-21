from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from pybot.app.ports.logger_port import LoggerPort


@dataclass
class CronController:
    start: Callable[[], None]
    stop: Callable[[], None]


def _seconds_until_next_minute() -> float:
    now = datetime.now(tz=UTC)
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    return max((next_minute - now).total_seconds(), 0.0)


def create_cron_cycle(task: Callable[[], None], logger: LoggerPort) -> CronController:
    schedule = "* * * * *"
    stop_event = threading.Event()
    thread: threading.Thread | None = None

    def _runner() -> None:
        while not stop_event.is_set():
            wait_seconds = _seconds_until_next_minute()
            if stop_event.wait(wait_seconds):
                break
            try:
                task()
            except Exception as error:
                logger.error("cron task failed", {"error": str(error)})

    def start() -> None:
        nonlocal thread
        if thread is not None and thread.is_alive():
            return
        logger.info("scheduler started (every 1 minute, UTC)", {"schedule": schedule})
        stop_event.clear()
        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

    def stop() -> None:
        logger.info("scheduler stopped")
        stop_event.set()
        if thread is not None:
            thread.join(timeout=5)

    return CronController(start=start, stop=stop)

