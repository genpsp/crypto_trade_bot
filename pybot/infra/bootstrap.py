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


@dataclass
class ModelRuntimeContext:
    model_id: str
    pair: str
    execution: ExecutionPort
    persistence: FirestoreRepository
    lock: RedisLockAdapter


def bootstrap() -> AppRuntime:
    env = load_env()
    logger = create_logger("bot")

    firestore = FirestoreClient.from_service_account_json(env.GOOGLE_APPLICATION_CREDENTIALS)
    redis = Redis.from_url(env.REDIS_URL, decode_responses=True)

    config_repo = FirestoreConfigRepository(firestore)
    market_data = OhlcvProvider()
    quote_client = JupiterQuoteClient()
    paper_execution: ExecutionPort = PaperExecutionAdapter(quote_client, logger)
    live_execution_by_wallet: dict[str, ExecutionPort] = {}

    def resolve_execution(mode: str, wallet_key_path: str | None) -> ExecutionPort:
        if mode == "PAPER":
            return paper_execution
        if wallet_key_path is None or wallet_key_path.strip() == "":
            raise RuntimeError("wallet_key_path is required in Firestore model metadata for LIVE mode")
        live_execution = live_execution_by_wallet.get(wallet_key_path)
        if live_execution is None:
            sender = SolanaSender(
                env.SOLANA_RPC_URL,
                wallet_key_path,
                env.WALLET_KEY_PASSPHRASE,
                logger,
            )
            live_execution = JupiterSwapAdapter(quote_client, sender, logger)
            live_execution_by_wallet[wallet_key_path] = live_execution
        return live_execution

    model_ids = config_repo.list_model_ids()
    if not model_ids:
        logger.warn("no models found in Firestore models collection")

    model_contexts: list[ModelRuntimeContext] = []
    runtime_summaries: list[dict[str, str]] = []
    for model_id in model_ids:
        try:
            startup_config = config_repo.get_current_config(model_id)
            model_metadata = config_repo.get_model_metadata(model_id)
        except Exception as error:
            logger.error(
                "failed to load model config",
                {
                    "model_id": model_id,
                    "error": str(error),
                },
            )
            continue

        if not startup_config["enabled"]:
            continue
        execution_config = startup_config["execution"]
        mode = execution_config["mode"]
        wallet_key_path = model_metadata.wallet_key_path
        if mode == "LIVE" and (wallet_key_path is None or wallet_key_path.strip() == ""):
            raise RuntimeError(
                f"models/{model_id}.wallet_key_path is required when execution.mode=LIVE"
            )
        wallet_key_path_for_log = wallet_key_path or ""

        context = ModelRuntimeContext(
            model_id=model_id,
            pair=startup_config["pair"],
            execution=resolve_execution(mode, wallet_key_path),
            persistence=FirestoreRepository(firestore, config_repo, mode=mode, model_id=model_id),
            lock=RedisLockAdapter(redis, logger, lock_namespace=model_id),
        )
        model_contexts.append(context)
        runtime_summaries.append(
            {
                "model_id": model_id,
                "mode": mode,
                "direction": startup_config["direction"],
                "strategy": startup_config["strategy"]["name"],
                "wallet_key_path": wallet_key_path_for_log,
            }
        )
        logger.info(
            "model runtime configured",
            {
                "model_id": model_id,
                "mode": mode,
                "direction": startup_config["direction"],
                "strategy": startup_config["strategy"]["name"],
                "trades_path": f"models/{model_id}/{context.persistence.trades_collection_name}",
                "runs_path": f"models/{model_id}/{context.persistence.runs_collection_name}",
                "wallet_key_path": wallet_key_path_for_log,
            },
        )

    if not model_contexts:
        logger.warn("no enabled models found in Firestore models collection")

    logger.info(
        "runtime models selected",
        {
            "enabled_models": runtime_summaries,
        },
    )

    def should_suppress_run_cycle_log(result: dict[str, str]) -> bool:
        return result.get("result") == "SKIPPED_ENTRY" and result.get("summary") in (
            "SKIPPED_ENTRY: entry already evaluated for this bar",
            "SKIPPED_ENTRY: idem entry key already exists for this bar",
        )

    def execute_cycle_for_model(context: ModelRuntimeContext) -> None:
        result = run_cycle(
            RunCycleDependencies(
                execution=context.execution,
                lock=context.lock,
                logger=logger,
                market_data=market_data,
                persistence=context.persistence,
                model_id=context.model_id,
            )
        )
        if should_suppress_run_cycle_log(result):  # type: ignore[arg-type]
            return
        logger.info(
            "run_cycle finished",
            {
                "model_id": context.model_id,
                "run_id": result.get("run_id"),
                "result": result.get("result"),
                "summary": result.get("summary"),
                "trade_id": result.get("trade_id"),
            },
        )

    def execute_cycle_for_all_models() -> None:
        for context in model_contexts:
            execute_cycle_for_model(context)

    def execute_scheduled_cycle() -> None:
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        is_five_minute_window = now.minute % 5 == 0
        for context in model_contexts:
            if is_five_minute_window:
                execute_cycle_for_model(context)
                continue
            open_trade = context.persistence.find_open_trade(context.pair)
            if open_trade is not None:
                execute_cycle_for_model(context)

    scheduler: CronController | None = None

    def start() -> None:
        nonlocal scheduler
        logger.info("bot startup: run first cycle immediately")
        execute_cycle_for_all_models()
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
