import { calculateEmaSeries } from '../indicators/ema';
import type {
  EntrySignalDecision,
  ExecutionConfig,
  ExitConfig,
  NoSignalDecision,
  OhlcvBar,
  RiskConfig,
  StrategyConfig,
  StrategyDecision
} from '../model/types';
import {
  calculateSwingLow,
  calculateTakeProfitPrice,
  tightenStopForLong
} from '../risk/swing_low_stop';

interface EvaluateInput {
  bars: OhlcvBar[];
  strategy: StrategyConfig;
  risk: RiskConfig;
  exit: ExitConfig;
  execution: ExecutionConfig;
}

function noSignal(
  summary: string,
  reason: string,
  emaFast?: number,
  emaSlow?: number
): NoSignalDecision {
  return {
    type: 'NO_SIGNAL',
    summary,
    reason,
    ema_fast: emaFast,
    ema_slow: emaSlow
  };
}

function entrySignal(
  summary: string,
  emaFast: number,
  emaSlow: number,
  entryPrice: number,
  stopPrice: number,
  takeProfitPrice: number
): EntrySignalDecision {
  return {
    type: 'ENTER',
    summary,
    ema_fast: emaFast,
    ema_slow: emaSlow,
    entry_price: entryPrice,
    stop_price: stopPrice,
    take_profit_price: takeProfitPrice
  };
}

export function evaluateEmaTrendPullbackV0(input: EvaluateInput): StrategyDecision {
  const { bars, strategy, risk, exit, execution } = input;
  const minimumBars = Math.max(
    strategy.ema_fast_period,
    strategy.ema_slow_period,
    strategy.swing_low_lookback_bars
  );

  if (bars.length < minimumBars) {
    return noSignal(
      'NO_SIGNAL: not enough bars for strategy calculation',
      `INSUFFICIENT_BARS_${bars.length}_OF_${minimumBars}`
    );
  }

  if (execution.min_notional_usdc <= 0) {
    return noSignal(
      'NO_SIGNAL: min_notional_usdc is invalid',
      'INVALID_MIN_NOTIONAL_USDC'
    );
  }

  const closes = bars.map((bar) => bar.close);
  const lows = bars.map((bar) => bar.low);

  const emaFastSeries = calculateEmaSeries(closes, strategy.ema_fast_period);
  const emaSlowSeries = calculateEmaSeries(closes, strategy.ema_slow_period);

  const emaFast = emaFastSeries.at(-1);
  const emaSlow = emaSlowSeries.at(-1);
  const entryPrice = closes.at(-1);

  if (
    emaFast === undefined ||
    emaSlow === undefined ||
    entryPrice === undefined ||
    Number.isNaN(emaFast) ||
    Number.isNaN(emaSlow)
  ) {
    return noSignal('NO_SIGNAL: EMA is not stable yet', 'EMA_NOT_STABLE');
  }

  if (emaFast <= emaSlow) {
    return noSignal(
      `NO_SIGNAL: trend filter failed (EMA${strategy.ema_fast_period}=${emaFast.toFixed(
        4
      )} <= EMA${strategy.ema_slow_period}=${emaSlow.toFixed(4)})`,
      'EMA_TREND_FILTER_FAILED',
      emaFast,
      emaSlow
    );
  }

  const swingLowStop = calculateSwingLow(lows, strategy.swing_low_lookback_bars);
  const stopPrice = tightenStopForLong(entryPrice, swingLowStop, risk.max_loss_per_trade_pct);
  if (stopPrice >= entryPrice) {
    return noSignal(
      'NO_SIGNAL: stop is not below entry',
      'INVALID_RISK_STRUCTURE',
      emaFast,
      emaSlow
    );
  }

  const takeProfitPrice = calculateTakeProfitPrice(
    entryPrice,
    stopPrice,
    exit.take_profit_r_multiple
  );

  return entrySignal(
    'ENTER: EMA trend passed on 4h close, open long spot position',
    emaFast,
    emaSlow,
    entryPrice,
    stopPrice,
    takeProfitPrice
  );
}
