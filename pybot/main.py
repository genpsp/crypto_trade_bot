from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

from pybot.infra.bootstrap import bootstrap
from pybot.infra.logging.logger import create_logger


def main() -> int:
    load_dotenv(dotenv_path=Path(".env"))
    logger = create_logger("bot")
    runtime = bootstrap()
    stop_event = threading.Event()

    def shutdown(sig_name: str) -> None:
        logger.info("received shutdown signal", {"signal": sig_name})
        runtime.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, lambda _signum, _frame: shutdown("SIGINT"))
    signal.signal(signal.SIGTERM, lambda _signum, _frame: shutdown("SIGTERM"))

    runtime.start()
    stop_event.wait()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logger = create_logger("bot")
        logger.error("bot startup failed", {"error": str(error)})
        raise SystemExit(1) from error
