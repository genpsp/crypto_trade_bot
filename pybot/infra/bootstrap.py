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


@dataclass(frozen=True)
class ModelRuntimeSpec:
    model_id: str
    pair: str
    mode: str
    direction: str
    strategy: str
    wallet_key_path: str


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

    model_contexts: dict[str, ModelRuntimeContext] = {}
    runtime_specs: dict[str, ModelRuntimeSpec] = {}
    warned_no_models = False
    warned_no_enabled_models = False

    def _build_runtime_summary(spec: ModelRuntimeSpec) -> dict[str, str]:
        return {
            "model_id": spec.model_id,
            "mode": spec.mode,
            "direction": spec.direction,
            "strategy": spec.strategy,
            "wallet_key_path": spec.wallet_key_path,
        }

    def _create_runtime_context(spec: ModelRuntimeSpec) -> ModelRuntimeContext:
        return ModelRuntimeContext(
            model_id=spec.model_id,
            pair=spec.pair,
            execution=resolve_execution(spec.mode, spec.wallet_key_path),
            persistence=FirestoreRepository(firestore, config_repo, mode=spec.mode, model_id=spec.model_id),
            lock=RedisLockAdapter(redis, logger, lock_namespace=spec.model_id),
        )

    def _load_enabled_model_specs() -> dict[str, ModelRuntimeSpec]:
        nonlocal warned_no_models
        model_ids = config_repo.list_model_ids()
        if not model_ids:
            if not warned_no_models:
                logger.warn("no models found in Firestore models collection")
            warned_no_models = True
            return {}
        warned_no_models = False

        specs: dict[str, ModelRuntimeSpec] = {}
        for model_id in model_ids:
            try:
                runtime_config = config_repo.get_current_config(model_id)
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

            if not runtime_config["enabled"]:
                continue

            mode = runtime_config["execution"]["mode"]
            wallet_key_path = (model_metadata.wallet_key_path or "").strip()
            if mode == "LIVE" and wallet_key_path == "":
                logger.error(
                    "skipping enabled LIVE model because wallet_key_path is missing",
                    {"model_id": model_id},
                )
                continue

            specs[model_id] = ModelRuntimeSpec(
                model_id=model_id,
                pair=runtime_config["pair"],
                mode=mode,
                direction=runtime_config["direction"],
                strategy=runtime_config["strategy"]["name"],
                wallet_key_path=wallet_key_path,
            )
        return specs

    def _refresh_model_contexts(*, force_log_runtime_summary: bool = False) -> list[ModelRuntimeContext]:
        nonlocal warned_no_enabled_models
        desired_specs = _load_enabled_model_specs()
        changed = False

        for model_id in list(model_contexts.keys()):
            if model_id in desired_specs:
                continue
            previous_spec = runtime_specs.get(model_id)
            model_contexts.pop(model_id, None)
            runtime_specs.pop(model_id, None)
            changed = True
            logger.info(
                "model runtime removed",
                {
                    "model_id": model_id,
                    "mode": previous_spec.mode if previous_spec else "",
                    "wallet_key_path": previous_spec.wallet_key_path if previous_spec else "",
                },
            )

        for model_id in sorted(desired_specs.keys()):
            desired_spec = desired_specs[model_id]
            current_spec = runtime_specs.get(model_id)
            if current_spec == desired_spec:
                continue

            context = _create_runtime_context(desired_spec)
            model_contexts[model_id] = context
            runtime_specs[model_id] = desired_spec
            changed = True

            action = "model runtime configured" if current_spec is None else "model runtime reconfigured"
            logger.info(
                action,
                {
                    "model_id": desired_spec.model_id,
                    "mode": desired_spec.mode,
                    "direction": desired_spec.direction,
                    "strategy": desired_spec.strategy,
                    "trades_path": f"models/{desired_spec.model_id}/{context.persistence.trades_collection_name}",
                    "runs_path": f"models/{desired_spec.model_id}/{context.persistence.runs_collection_name}",
                    "wallet_key_path": desired_spec.wallet_key_path,
                },
            )

        if not model_contexts:
            if force_log_runtime_summary or changed or not warned_no_enabled_models:
                logger.warn("no enabled models found in Firestore models collection")
            warned_no_enabled_models = True
        else:
            warned_no_enabled_models = False

        if changed or force_log_runtime_summary:
            runtime_summaries = [_build_runtime_summary(runtime_specs[mid]) for mid in sorted(runtime_specs.keys())]
            logger.info(
                "runtime models selected",
                {
                    "enabled_models": runtime_summaries,
                },
            )

        return [model_contexts[mid] for mid in sorted(model_contexts.keys())]

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
        contexts = _refresh_model_contexts()
        for context in contexts:
            execute_cycle_for_model(context)

    def execute_scheduled_cycle() -> None:
        from datetime import UTC, datetime

        contexts = _refresh_model_contexts()
        now = datetime.now(tz=UTC)
        is_five_minute_window = now.minute % 5 == 0
        for context in contexts:
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
        contexts = _refresh_model_contexts(force_log_runtime_summary=True)
        for context in contexts:
            execute_cycle_for_model(context)
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
