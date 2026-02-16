import type { TradeState } from './trade_state';

export type Network = 'mainnet-beta';
export type Pair = 'SOL/USDC';
export type Direction = 'LONG_ONLY';
export type SignalTimeframe = '4h';

export interface StrategyConfig {
  name: 'ema_trend_pullback_v0';
  ema_fast_period: number;
  ema_slow_period: number;
  swing_low_lookback_bars: number;
  entry: 'ON_4H_CLOSE';
}

export interface RiskConfig {
  max_loss_per_trade_pct: number;
  max_trades_per_day: number;
}

export interface ExecutionConfig {
  mode: 'PAPER' | 'LIVE';
  swap_provider: 'JUPITER';
  slippage_bps: number;
  min_notional_usdc: number;
  only_direct_routes: boolean;
}

export interface ExitConfig {
  stop: 'SWING_LOW';
  take_profit_r_multiple: number;
}

export interface MetaConfig {
  config_version: number;
  note: string;
}

export interface BotConfig {
  enabled: boolean;
  network: Network;
  pair: Pair;
  direction: Direction;
  signal_timeframe: SignalTimeframe;
  strategy: StrategyConfig;
  risk: RiskConfig;
  execution: ExecutionConfig;
  exit: ExitConfig;
  meta: MetaConfig;
}

export interface OhlcvBar {
  openTime: Date;
  closeTime: Date;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface EntrySignalDecision {
  type: 'ENTER';
  summary: string;
  ema_fast: number;
  ema_slow: number;
  entry_price: number;
  stop_price: number;
  take_profit_price: number;
}

export interface NoSignalDecision {
  type: 'NO_SIGNAL';
  summary: string;
  reason: string;
  ema_fast?: number;
  ema_slow?: number;
}

export type StrategyDecision = EntrySignalDecision | NoSignalDecision;

export interface TradeSignalSnapshot {
  summary: string;
  bar_close_time_iso: string;
  ema_fast: number;
  ema_slow: number;
}

export interface TradePlanSnapshot {
  summary: string;
  notional_usdc: number;
  entry_price: number;
  stop_price: number;
  take_profit_price: number;
  r_multiple: number;
}

export interface TradeExecutionSnapshot {
  entry_tx_signature?: string;
  exit_tx_signature?: string;
  exit_submission_state?: 'SUBMITTED' | 'CONFIRMED' | 'FAILED';
  entry_error?: string;
  exit_error?: string;
  order?: TradeOrderSnapshot;
  result?: TradeResultSnapshot;
  exit_order?: TradeOrderSnapshot;
  exit_result?: TradeResultSnapshot;
}

export interface TradeOrderSnapshot {
  tx_signature: string;
}

export interface TradeResultSnapshot {
  status: 'SIMULATED';
  avg_fill_price: number;
  spent_quote_usdc: number;
  filled_base_sol: number;
}

export interface TradePositionSnapshot {
  status: 'OPEN' | 'CLOSED';
  quantity_sol: number;
  entry_price: number;
  stop_price: number;
  take_profit_price: number;
  entry_time_iso?: string;
  exit_price?: number;
  exit_trigger_price?: number;
  exit_time_iso?: string;
}

export type CloseReason = 'TAKE_PROFIT' | 'STOP_LOSS' | 'MANUAL' | 'SYSTEM_ERROR';

export interface TradeRecord {
  trade_id: string;
  bar_close_time_iso: string;
  pair: Pair;
  direction: Direction;
  state: TradeState;
  config_version: number;
  signal: TradeSignalSnapshot;
  plan: TradePlanSnapshot;
  execution: TradeExecutionSnapshot;
  position: TradePositionSnapshot;
  close_reason?: CloseReason;
  created_at: string;
  updated_at: string;
}

export type RunResult =
  | 'OPENED'
  | 'CLOSED'
  | 'NO_SIGNAL'
  | 'HOLD'
  | 'SKIPPED'
  | 'SKIPPED_ENTRY'
  | 'FAILED';

export interface RunRecord {
  run_id: string;
  bar_close_time_iso: string;
  executed_at_iso: string;
  result: RunResult;
  summary: string;
  reason?: string;
  config_version?: number;
  trade_id?: string;
}
