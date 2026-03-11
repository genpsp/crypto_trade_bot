from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import threading
from typing import Any, Callable

from google.cloud.firestore import Client as FirestoreClient
from redis import Redis

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.adapters.execution.paper_execution import PaperExecutionAdapter
from apps.gmo_bot.adapters.lock.redis_lock import RedisLockAdapter
from apps.gmo_bot.adapters.market_data.ohlcv_provider import OhlcvProvider
from apps.gmo_bot.adapters.persistence.firestore_repo import FirestoreRepository
from apps.gmo_bot.app.ports.execution_port import ExecutionPort
from apps.gmo_bot.app.usecases.run_cycle import RunCycleDependencies, run_cycle
from apps.gmo_bot.domain.model.types import TradeRecord
from apps.gmo_bot.infra.alerting import SlackAlertConfig, SlackNotifier, is_execution_error_result
from apps.gmo_bot.infra.config.env import load_env
from apps.gmo_bot.infra.config.firestore_config_repo import (
    GLOBAL_CONTROL_COLLECTION_ID,
    GLOBAL_CONTROL_DOC_ID,
    GLOBAL_CONTROL_PAUSE_FIELD,
    MODELS_COLLECTION_ID,
    FirestoreConfigRepository,
)
from apps.gmo_bot.infra.logging.logger import create_logger
from apps.gmo_bot.infra.scheduler.cron_cycle import CronController, create_cron_cycle

CONSECUTIVE_FAILURE_ALERT_THRESHOLD = 3
STALE_CYCLE_ALERT_MINUTES = 10
DUPLICATE_ALERT_SUPPRESSION_SECONDS = 300
RUNTIME_REFRESH_FALLBACK_INTERVAL_SECONDS = 900


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
    broker: str
    config_fingerprint: str


def _should_execute_cycle(*, is_five_minute_window: bool, has_open_trade: bool, pause_all: bool) -> bool:
    if pause_all:
        return has_open_trade
    if is_five_minute_window:
        return True
    return has_open_trade


def bootstrap() -> AppRuntime:
    env = load_env()
    logger = create_logger("gmo-bot")
    firestore = FirestoreClient.from_service_account_json(env.GOOGLE_APPLICATION_CREDENTIALS)
    redis = Redis.from_url(env.REDIS_URL, decode_responses=True)
    config_repo = FirestoreConfigRepository(firestore)
    api_client = GmoApiClient(env.GMO_API_KEY, env.GMO_API_SECRET)
    market_data = OhlcvProvider(client=api_client, redis=redis)
    paper_execution: ExecutionPort = PaperExecutionAdapter(logger)
    live_execution: ExecutionPort = GmoMarginExecutionAdapter(api_client, logger)

    model_contexts: dict[str, ModelRuntimeContext] = {}
    runtime_specs: dict[str, ModelRuntimeSpec] = {}
    runtime_pause_state: bool | None = None
    last_runtime_refresh_at: datetime | None = None
    runtime_refresh_needed = threading.Event()
    runtime_refresh_needed.set()
    pause_refresh_needed = threading.Event()
    pause_refresh_needed.set()
    listener_state_lock = threading.Lock()
    models_collection_listener: Any | None = None
    global_control_listener: Any | None = None
    model_config_listeners: dict[str, Any] = {}
    notifier = SlackNotifier(
        config=SlackAlertConfig(
            webhook_url=env.SLACK_WEBHOOK_URL,
            consecutive_failure_threshold=CONSECUTIVE_FAILURE_ALERT_THRESHOLD,
            stale_minutes=STALE_CYCLE_ALERT_MINUTES,
            duplicate_suppression_seconds=DUPLICATE_ALERT_SUPPRESSION_SECONDS,
        ),
        logger=logger,
        dedupe_store=redis,
        dedupe_namespace="gmo_bot",
    )
    failure_streaks_by_model: dict[str, int] = {}
    last_cycle_completed_at = datetime.now(tz=UTC)
    stale_cycle_alert_active = False
    cycle_state_lock = threading.Lock()
    watchdog_stop_event = threading.Event()
    watchdog_thread: threading.Thread | None = None
    warned_no_enabled_models = False

    def _build_runtime_summary(spec: ModelRuntimeSpec) -> dict[str, str]:
        return {
            "model_id": spec.model_id,
            "mode": spec.mode,
            "direction": spec.direction,
            "strategy": spec.strategy,
            "broker": spec.broker,
            "config_fingerprint": spec.config_fingerprint,
        }

    def _unsubscribe_watch(handle: Any) -> None:
        if handle is None:
            return
        unsubscribe = getattr(handle, "unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception:
                pass

    def _mark_runtime_refresh_needed(reason: str) -> None:
        if runtime_refresh_needed.is_set():
            return
        runtime_refresh_needed.set()
        logger.info("runtime refresh scheduled from Firestore change", {"reason": reason})

    def _on_models_collection_snapshot(_docs: Any, _changes: Any, _read_time: Any) -> None:
        _mark_runtime_refresh_needed("models_collection_changed")

    def _on_model_config_snapshot(model_id: str, _docs: Any, _changes: Any, _read_time: Any) -> None:
        _mark_runtime_refresh_needed(f"config_changed:{model_id}")

    def _mark_pause_refresh_needed(reason: str) -> None:
        if pause_refresh_needed.is_set():
            return
        pause_refresh_needed.set()
        logger.info("pause refresh scheduled from Firestore change", {"reason": reason})

    def _on_global_control_snapshot(_docs: Any, _changes: Any, _read_time: Any) -> None:
        _mark_pause_refresh_needed("control_global_changed")

    def _sync_model_config_watchers(model_ids: list[str]) -> None:
        with listener_state_lock:
            target_ids = set(model_ids)
            current_ids = set(model_config_listeners.keys())
            for removed_id in current_ids - target_ids:
                _unsubscribe_watch(model_config_listeners.pop(removed_id, None))
            for added_id in sorted(target_ids - current_ids):
                config_doc_ref = firestore.collection(MODELS_COLLECTION_ID).document(added_id).collection("config").document("current")
                model_config_listeners[added_id] = config_doc_ref.on_snapshot(
                    lambda docs, changes, read_time, mid=added_id: _on_model_config_snapshot(mid, docs, changes, read_time)
                )

    def _start_firestore_watchers() -> None:
        nonlocal models_collection_listener, global_control_listener
        with listener_state_lock:
            if models_collection_listener is None:
                models_collection_listener = firestore.collection(MODELS_COLLECTION_ID).on_snapshot(_on_models_collection_snapshot)
            if global_control_listener is None:
                global_control_listener = firestore.collection(GLOBAL_CONTROL_COLLECTION_ID).document(GLOBAL_CONTROL_DOC_ID).on_snapshot(_on_global_control_snapshot)
            existing_model_ids = config_repo.list_model_ids()
        _sync_model_config_watchers(existing_model_ids)

    def _stop_firestore_watchers() -> None:
        nonlocal models_collection_listener, global_control_listener
        with listener_state_lock:
            _unsubscribe_watch(models_collection_listener)
            models_collection_listener = None
            _unsubscribe_watch(global_control_listener)
            global_control_listener = None
            for listener in model_config_listeners.values():
                _unsubscribe_watch(listener)
            model_config_listeners.clear()

    def _active_model_contexts() -> list[ModelRuntimeContext]:
        return [model_contexts[mid] for mid in sorted(model_contexts.keys())]

    def _current_runtime_summaries() -> list[dict[str, str]]:
        return [_build_runtime_summary(runtime_specs[mid]) for mid in sorted(runtime_specs.keys())]

    def _mark_cycle_completed() -> None:
        nonlocal last_cycle_completed_at, stale_cycle_alert_active
        with cycle_state_lock:
            last_cycle_completed_at = datetime.now(tz=UTC)
            recovered = stale_cycle_alert_active
            stale_cycle_alert_active = False
        if recovered:
            notifier.notify_stale_cycle_recovered(model_ids=sorted(runtime_specs.keys()))

    def _apply_failure_streak_and_alert(result: dict[str, str | None], model_id: str) -> None:
        run_result = str(result.get("result") or "")
        summary = str(result.get("summary") or "")
        run_id = result.get("run_id")
        trade_id = result.get("trade_id")

        if is_execution_error_result(run_result, summary):
            notifier.notify_trade_error(
                model_id=model_id,
                result=run_result,
                summary=summary,
                run_id=run_id,
                trade_id=trade_id,
            )

        threshold = CONSECUTIVE_FAILURE_ALERT_THRESHOLD
        if run_result == "FAILED":
            streak = failure_streaks_by_model.get(model_id, 0) + 1
            failure_streaks_by_model[model_id] = streak
            if streak >= threshold and (streak == threshold or streak % threshold == 0):
                notifier.notify_consecutive_failures(
                    model_id=model_id,
                    streak=streak,
                    threshold=threshold,
                    run_id=run_id,
                    summary=summary,
                )
            return

        previous_streak = failure_streaks_by_model.get(model_id, 0)
        if previous_streak >= threshold:
            notifier.notify_failure_streak_recovered(
                model_id=model_id,
                previous_streak=previous_streak,
                latest_result=run_result,
                summary=summary,
            )
        failure_streaks_by_model[model_id] = 0

    def _refresh_runtime_specs() -> None:
        nonlocal runtime_pause_state, last_runtime_refresh_at, warned_no_enabled_models
        refreshed_contexts: dict[str, ModelRuntimeContext] = {}
        refreshed_specs: dict[str, ModelRuntimeSpec] = {}
        all_model_ids = config_repo.list_model_ids()
        for model_id in all_model_ids:
            metadata = config_repo.get_model_metadata(model_id)
            config = config_repo.get_current_config(model_id)
            if not config["enabled"]:
                continue
            strategy_name = config["strategy"]["name"]
            spec = ModelRuntimeSpec(
                model_id=model_id,
                pair=config["pair"],
                mode=metadata.mode,
                direction=metadata.direction,
                strategy=strategy_name,
                broker=config["broker"],
                config_fingerprint=hashlib.sha1(repr(config).encode("utf-8")).hexdigest()[:12],
            )
            execution = paper_execution if metadata.mode == "PAPER" else live_execution
            refreshed_specs[model_id] = spec
            refreshed_contexts[model_id] = ModelRuntimeContext(
                model_id=model_id,
                pair=config["pair"],
                execution=execution,
                persistence=FirestoreRepository(firestore, config_repo, mode=metadata.mode, model_id=model_id),
                lock=RedisLockAdapter(redis=redis, logger=logger, lock_namespace=model_id),
            )
            logger.info("model runtime configured", _build_runtime_summary(spec))
        model_contexts.clear()
        model_contexts.update(refreshed_contexts)
        runtime_specs.clear()
        runtime_specs.update(refreshed_specs)
        runtime_pause_state = config_repo.is_global_pause_enabled()
        last_runtime_refresh_at = datetime.now(tz=UTC)
        if refreshed_specs:
            warned_no_enabled_models = False
            logger.info(
                "runtime models selected",
                {"enabled_models": [_build_runtime_summary(spec) for spec in refreshed_specs.values()]},
            )
        elif not warned_no_enabled_models:
            warned_no_enabled_models = True
            logger.warn("no enabled models found in Firestore models collection")
        _sync_model_config_watchers(all_model_ids)

    def _refresh_runtime_if_needed(force: bool = False) -> None:
        should_refresh = force or runtime_refresh_needed.is_set()
        if not should_refresh and last_runtime_refresh_at is not None:
            elapsed = (datetime.now(tz=UTC) - last_runtime_refresh_at).total_seconds()
            should_refresh = elapsed >= RUNTIME_REFRESH_FALLBACK_INTERVAL_SECONDS
        if should_refresh:
            _refresh_runtime_specs()
            runtime_refresh_needed.clear()

    def _refresh_pause_if_needed(force: bool = False) -> None:
        nonlocal runtime_pause_state
        if force or pause_refresh_needed.is_set() or runtime_pause_state is None:
            runtime_pause_state = config_repo.is_global_pause_enabled()
            pause_refresh_needed.clear()

    def _watchdog_loop() -> None:
        nonlocal stale_cycle_alert_active
        threshold_minutes = STALE_CYCLE_ALERT_MINUTES
        threshold_seconds = threshold_minutes * 60
        interval_seconds = max(15, min(60, threshold_seconds // 3))
        if interval_seconds <= 0:
            interval_seconds = 15

        while not watchdog_stop_event.wait(interval_seconds):
            if not runtime_specs:
                continue
            with cycle_state_lock:
                elapsed_seconds = int((datetime.now(tz=UTC) - last_cycle_completed_at).total_seconds())
                should_alert = elapsed_seconds >= threshold_seconds and not stale_cycle_alert_active
                if should_alert:
                    stale_cycle_alert_active = True
            if should_alert:
                notifier.notify_stale_cycle(
                    elapsed_seconds=elapsed_seconds,
                    threshold_minutes=threshold_minutes,
                    model_ids=sorted(runtime_specs.keys()),
                )

    def _run_model_cycle(context: ModelRuntimeContext, pause_all: bool) -> None:
        open_trade = context.persistence.find_open_trade(context.pair)
        now = datetime.now(tz=UTC)
        is_five_minute_window = now.minute % 5 == 0
        if not _should_execute_cycle(
            is_five_minute_window=is_five_minute_window,
            has_open_trade=open_trade is not None,
            pause_all=pause_all,
        ):
            return
        result = run_cycle(
            RunCycleDependencies(
                execution=context.execution,
                lock=context.lock,
                logger=logger,
                market_data=market_data,
                persistence=context.persistence,
                model_id=context.model_id,
                prefetched_open_trade=open_trade,
                use_prefetched_open_trade=True,
            )
        )
        _apply_failure_streak_and_alert(result, context.model_id)
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

    def _run_all_models() -> None:
        _refresh_runtime_if_needed()
        _refresh_pause_if_needed()
        pause_all = bool(runtime_pause_state)
        for context in _active_model_contexts():
            _run_model_cycle(context, pause_all)
        _mark_cycle_completed()

    cron_controller: CronController | None = None

    def start() -> None:
        nonlocal cron_controller, watchdog_thread
        _refresh_runtime_if_needed(force=True)
        _refresh_pause_if_needed(force=True)
        _start_firestore_watchers()
        logger.info("bot startup: run first cycle immediately")
        _run_all_models()
        cron_controller = create_cron_cycle(_run_all_models, logger)
        cron_controller.start()
        watchdog_stop_event.clear()
        watchdog_thread = threading.Thread(target=_watchdog_loop, name="gmo-bot-watchdog", daemon=True)
        watchdog_thread.start()
        notifier.notify_startup(_current_runtime_summaries())

    def stop() -> None:
        nonlocal cron_controller, watchdog_thread
        notifier.notify_shutdown(reason="shutdown signal received")
        watchdog_stop_event.set()
        if cron_controller is not None:
            cron_controller.stop()
            cron_controller = None
        if watchdog_thread is not None:
            watchdog_thread.join(timeout=2)
            watchdog_thread = None
        _stop_firestore_watchers()
        logger.info("bot stopped")

    return AppRuntime(start=start, stop=stop)
