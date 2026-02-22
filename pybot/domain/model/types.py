from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

Network = Literal["mainnet-beta"]
Pair = Literal["SOL/USDC"]
Direction = Literal["LONG_ONLY", "SHORT_ONLY"]
SignalTimeframe = Literal["2h", "4h"]
StrategyName = Literal["ema_trend_pullback_v0", "storm_short_v0"]


class StrategyConfig(TypedDict):
    name: StrategyName
    ema_fast_period: int
    ema_slow_period: int
    swing_low_lookback_bars: int
    entry: Literal["ON_BAR_CLOSE"]


class RiskConfig(TypedDict):
    max_loss_per_trade_pct: float
    max_trades_per_day: int
    volatile_atr_pct_threshold: float
    storm_atr_pct_threshold: float
    volatile_size_multiplier: float
    storm_size_multiplier: float


class ExecutionConfig(TypedDict):
    mode: Literal["PAPER", "LIVE"]
    swap_provider: Literal["JUPITER"]
    slippage_bps: int
    min_notional_usdc: float
    only_direct_routes: bool


class ExitConfig(TypedDict):
    stop: Literal["SWING_LOW"]
    take_profit_r_multiple: float


class MetaConfig(TypedDict):
    config_version: int
    note: str


class ModelConfig(TypedDict):
    model_id: str
    enabled: bool
    direction: Direction
    wallet_key_path: str | None
    strategy: StrategyConfig
    risk: RiskConfig
    exit: ExitConfig


class BotConfig(TypedDict):
    enabled: bool
    network: Network
    pair: Pair
    direction: Direction
    signal_timeframe: SignalTimeframe
    strategy: StrategyConfig
    risk: RiskConfig
    execution: ExecutionConfig
    exit: ExitConfig
    meta: MetaConfig
    models: list[ModelConfig]


@dataclass
class OhlcvBar:
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


VolatilityRegime = Literal["NORMAL", "VOLATILE", "STORM"]


class StrategyDiagnostics(TypedDict, total=False):
    bars_count: int
    minimum_bars_required: int
    ema_fast: float
    ema_slow: float
    previous_close: float
    previous_ema_fast: float
    pullback_found: bool
    reclaim_found: bool
    distance_from_ema_fast_pct: float
    rsi: float
    atr: float
    swing_low_stop: float
    stop_candidate: float
    final_stop: float
    stop_distance_pct: float
    take_profit_price: float
    atr_pct: float
    volatility_regime: VolatilityRegime
    position_size_multiplier: float


@dataclass
class EntrySignalDecision:
    type: Literal["ENTER"]
    summary: str
    ema_fast: float
    ema_slow: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    diagnostics: StrategyDiagnostics | None = None


@dataclass
class NoSignalDecision:
    type: Literal["NO_SIGNAL"]
    summary: str
    reason: str
    ema_fast: float | None = None
    ema_slow: float | None = None
    diagnostics: StrategyDiagnostics | None = None


StrategyDecision = EntrySignalDecision | NoSignalDecision


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
    notional_usdc: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    r_multiple: float


class TradeOrderSnapshot(TypedDict):
    tx_signature: str


class TradeResultSnapshot(TypedDict):
    status: Literal["SIMULATED", "ESTIMATED", "CONFIRMED"]
    avg_fill_price: float
    spent_quote_usdc: float
    filled_base_sol: float


class TradeExecutionSnapshot(TypedDict, total=False):
    entry_tx_signature: str
    exit_tx_signature: str
    exit_submission_state: Literal["SUBMITTED", "CONFIRMED", "FAILED"]
    entry_error: str
    exit_error: str
    order: TradeOrderSnapshot
    result: TradeResultSnapshot
    entry_order: TradeOrderSnapshot
    entry_result: TradeResultSnapshot
    exit_order: TradeOrderSnapshot
    exit_result: TradeResultSnapshot


class TradePositionSnapshot(TypedDict, total=False):
    status: Literal["OPEN", "CLOSED"]
    quantity_sol: float
    quote_amount_usdc: float
    entry_trigger_price: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    entry_time_iso: str
    exit_price: float
    exit_trigger_price: float
    exit_time_iso: str


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


def decision_to_dict(decision: StrategyDecision) -> dict[str, Any]:
    if isinstance(decision, EntrySignalDecision):
        return {
            "type": decision.type,
            "summary": decision.summary,
            "ema_fast": decision.ema_fast,
            "ema_slow": decision.ema_slow,
            "entry_price": decision.entry_price,
            "stop_price": decision.stop_price,
            "take_profit_price": decision.take_profit_price,
            "diagnostics": decision.diagnostics,
        }

    return {
        "type": decision.type,
        "summary": decision.summary,
        "reason": decision.reason,
        "ema_fast": decision.ema_fast,
        "ema_slow": decision.ema_slow,
        "diagnostics": decision.diagnostics,
    }
