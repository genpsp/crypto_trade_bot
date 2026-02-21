from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from google.cloud.firestore import Client as FirestoreClient
from redis import Redis

from pybot.adapters.execution.jupiter_quote_client import JupiterQuoteClient
from pybot.adapters.execution.jupiter_swap import JupiterSwapAdapter
from pybot.adapters.execution.paper_execution import PaperExecutionAdapter
from pybot.adapters.execution.solana_sender import SolanaSender
from pybot.adapters.lock.redis_lock import RedisLockAdapter
from pybot.adapters.market_data.ohlcv_provider import OhlcvProvider
from pybot.adapters.persistence.firestore_repo import FirestoreRepository
from pybot.app.ports.execution_port import ExecutionPort
from pybot.app.usecases.run_cycle import RunCycleDependencies, run_cycle
from pybot.infra.config.env import load_env
from pybot.infra.config.firestore_config_repo import FirestoreConfigRepository
from pybot.infra.logging.logger import create_logger
from pybot.infra.scheduler.cron_cycle import CronController, create_cron_cycle


@dataclass
class AppRuntime:
    start: Callable[[], None]
    stop: Callable[[], None]


def bootstrap() -> AppRuntime:
    env = load_env()
    logger = create_logger("bot")

    firestore = FirestoreClient.from_service_account_json(env.GOOGLE_APPLICATION_CREDENTIALS)
    redis = Redis.from_url(env.REDIS_URL, decode_responses=True)

    config_repo = FirestoreConfigRepository(firestore)
    startup_config = config_repo.get_current_config()
    mode = startup_config["execution"]["mode"]
    collections = (
        {"trades": "paper_trades", "runs": "paper_runs"}
        if mode == "PAPER"
        else {"trades": "trades", "runs": "runs"}
    )

    persistence = FirestoreRepository(firestore, config_repo, collections)
    lock = RedisLockAdapter(redis, logger)
    market_data = OhlcvProvider()
    quote_client = JupiterQuoteClient()

    execution: ExecutionPort
    if mode == "PAPER":
        execution = PaperExecutionAdapter(quote_client, logger)
    else:
        sender = SolanaSender(
            env.SOLANA_RPC_URL,
            env.WALLET_KEY_PATH,
            env.WALLET_KEY_PASSPHRASE,
            logger,
        )
        execution = JupiterSwapAdapter(quote_client, sender, logger)

    logger.info(
        "runtime mode selected",
        {
            "mode": mode,
            "trades_collection": collections["trades"],
            "runs_collection": collections["runs"],
        },
    )

    def should_suppress_run_cycle_log(result: dict[str, str]) -> bool:
        return result.get("result") == "SKIPPED_ENTRY" and result.get("summary") in (
            "SKIPPED_ENTRY: entry already evaluated for this bar",
            "SKIPPED_ENTRY: idem entry key already exists for this bar",
        )

    def execute_cycle() -> None:
        result = run_cycle(
            RunCycleDependencies(
                execution=execution,
                lock=lock,
                logger=logger,
                market_data=market_data,
                persistence=persistence,
            )
        )
        if should_suppress_run_cycle_log(result):  # type: ignore[arg-type]
            return
        logger.info(
            "run_cycle finished",
            {
                "run_id": result.get("run_id"),
                "result": result.get("result"),
                "summary": result.get("summary"),
                "trade_id": result.get("trade_id"),
            },
        )

    def execute_scheduled_cycle() -> None:
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        is_five_minute_window = now.minute % 5 == 0
        if not is_five_minute_window:
            open_trade = persistence.find_open_trade(startup_config["pair"])
            if open_trade is None:
                return
        execute_cycle()

    scheduler: CronController | None = None

    def start() -> None:
        nonlocal scheduler
        logger.info("bot startup: run first cycle immediately")
        execute_cycle()
        scheduler = create_cron_cycle(execute_scheduled_cycle, logger)
        scheduler.start()

    def stop() -> None:
        if scheduler is not None:
            scheduler.stop()
        try:
            redis.close()
        except Exception:
            pass
        logger.info("bot stopped")

    return AppRuntime(start=start, stop=stop)
