from __future__ import annotations

from typing import Any, Literal, TypedDict

from apps.dex_bot.domain.model.types import (
    Direction,
    EntrySignalDecision,
    ExitConfig,
    ModelDirection,
    NoSignalDecision,
    OhlcvBar,
    RiskConfig,
    SignalTimeframe,
    StrategyConfig,
    StrategyDecision,
)

Pair = Literal["SOL/JPY"]
Broker = Literal["GMO_COIN"]


class ExecutionConfig(TypedDict):
    mode: Literal["PAPER", "LIVE"]
    broker: Broker
    slippage_bps: int
    min_notional_jpy: float
    leverage_multiplier: float
    margin_usage_ratio: float


class MetaConfig(TypedDict):
    config_version: int
    note: str


class BotConfig(TypedDict):
    enabled: bool
    broker: Broker
    pair: Pair
    direction: ModelDirection
    signal_timeframe: SignalTimeframe
    strategy: StrategyConfig
    risk: RiskConfig
    execution: ExecutionConfig
    exit: ExitConfig
    meta: MetaConfig


class PositionLotSnapshot(TypedDict):
    position_id: int
    size_sol: float


class TradeSignalSnapshot(TypedDict):
    summary: str
    bar_close_time_iso: str
    ema_fast: float
    ema_slow: float
    entry_price: float
    stop_price: float
    take_profit_price: float


class TradePlanSnapshot(TypedDict):
    summary: str
    notional_jpy: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    r_multiple: float


class TradeOrderSnapshot(TypedDict):
    order_id: int


class TradeResultSnapshot(TypedDict, total=False):
    status: Literal["SIMULATED", "ESTIMATED", "CONFIRMED"]
    avg_fill_price: float
    filled_base_sol: float
    filled_quote_jpy: float
    fee_jpy: float
    realized_pnl_jpy: float
    execution_ids: list[str]
    lots: list[PositionLotSnapshot]


class TradeExecutionSnapshot(TypedDict, total=False):
    entry_order_id: int
    exit_order_id: int
    take_profit_order_id: int
    stop_loss_order_id: int
    entry_reference_price: float
    exit_reference_price: float
    take_profit_order_status: str
    stop_loss_order_status: str
    entry_submission_state: Literal["SUBMITTED", "CONFIRMED", "FAILED"]
    exit_submission_state: Literal["SUBMITTED", "CONFIRMED", "FAILED"]
    entry_fee_jpy: float
    exit_fee_jpy: float
    realized_pnl_jpy: float
    entry_error: str
    exit_error: str
    protective_exit_error: str
    entry_order: TradeOrderSnapshot
    entry_result: TradeResultSnapshot
    exit_order: TradeOrderSnapshot
    exit_result: TradeResultSnapshot
    take_profit_order: TradeOrderSnapshot
    stop_loss_order: TradeOrderSnapshot


class TradePositionSnapshot(TypedDict, total=False):
    status: Literal["OPEN", "CLOSED"]
    quantity_sol: float
    quote_amount_jpy: float
    entry_trigger_price: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    entry_time_iso: str
    exit_price: float
    exit_trigger_price: float
    exit_time_iso: str
    lots: list[PositionLotSnapshot]


CloseReason = Literal["TAKE_PROFIT", "STOP_LOSS", "MANUAL", "SYSTEM_ERROR"]
TradeState = Literal["CREATED", "SUBMITTED", "CONFIRMED", "CLOSED", "FAILED", "CANCELED"]


class TradeRecord(TypedDict, total=False):
    trade_id: str
    model_id: str
    bar_close_time_iso: str
    pair: Pair
    direction: Direction
    state: TradeState
    config_version: int
    signal: TradeSignalSnapshot
    plan: TradePlanSnapshot
    execution: TradeExecutionSnapshot
    position: TradePositionSnapshot
    close_reason: CloseReason
    created_at: str
    updated_at: str


RunResult = Literal[
    "OPENED",
    "CLOSED",
    "PARTIALLY_CLOSED",
    "NO_SIGNAL",
    "HOLD",
    "SKIPPED",
    "SKIPPED_ENTRY",
    "FAILED",
]


class RunRecord(TypedDict, total=False):
    run_id: str
    model_id: str
    bar_close_time_iso: str
    executed_at_iso: str
    run_date: str
    result: RunResult
    summary: str
    reason: str
    occurrence_count: int
    first_executed_at_iso: str
    last_executed_at_iso: str
    latest_run_id: str
    config_version: int
    trade_id: str
    metrics: dict[str, Any]


__all__ = [
    "BotConfig",
    "CloseReason",
    "Direction",
    "EntrySignalDecision",
    "ExecutionConfig",
    "ExitConfig",
    "MetaConfig",
    "ModelDirection",
    "NoSignalDecision",
    "OhlcvBar",
    "Pair",
    "PositionLotSnapshot",
    "RiskConfig",
    "RunRecord",
    "RunResult",
    "SignalTimeframe",
    "StrategyConfig",
    "StrategyDecision",
    "TradeExecutionSnapshot",
    "TradeOrderSnapshot",
    "TradePlanSnapshot",
    "TradePositionSnapshot",
    "TradeRecord",
    "TradeResultSnapshot",
    "TradeSignalSnapshot",
    "TradeState",
]
