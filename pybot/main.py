from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

from pybot.infra.bootstrap import bootstrap


def main() -> int:
    load_dotenv(dotenv_path=Path(".env"))
    runtime = bootstrap()
    stop_event = threading.Event()

    def shutdown(sig_name: str) -> None:
        print(f"[INFO] received {sig_name}, shutting down...")
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
        print(f"[ERROR] bot startup failed {error}")
        raise SystemExit(1) from error

