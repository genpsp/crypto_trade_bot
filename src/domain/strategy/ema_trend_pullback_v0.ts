import { atrSeries, emaSeries, rsiSeries } from '../indicators/ta';
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

const PULLBACK_LOOKBACK_BARS = 4;
const MAX_DISTANCE_FROM_EMA_FAST_PCT = 0.8;
const MIN_STOP_DISTANCE_PCT = 0.4;
const RSI_PERIOD = 14;
const RSI_LOWER_BOUND = 45;
const RSI_UPPER_BOUND = 70;
const ATR_PERIOD = 14;
const ATR_STOP_MULTIPLIER = 2;

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
    strategy.swing_low_lookback_bars,
    PULLBACK_LOOKBACK_BARS + 1,
    RSI_PERIOD + 1,
    ATR_PERIOD + 1
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
  const highs = bars.map((bar) => bar.high);
  const lows = bars.map((bar) => bar.low);

  const emaFastSeries = emaSeries(closes, strategy.ema_fast_period);
  const emaSlowSeries = emaSeries(closes, strategy.ema_slow_period);
  const emaFastOffset = closes.length - emaFastSeries.length;
  const emaFastByBar = closes.map((_, index) => {
    const emaIndex = index - emaFastOffset;
    return emaIndex >= 0 ? emaFastSeries[emaIndex] : undefined;
  });

  const emaFast = emaFastByBar.at(-1);
  const emaSlow = emaSlowSeries.at(-1);
  const entryPrice = closes.at(-1);
  const previousClose = closes.at(-2);
  const previousEmaFast = emaFastByBar.at(-2);

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

  const latestIndex = bars.length - 1;
  const pullbackStartIndex = Math.max(0, latestIndex - PULLBACK_LOOKBACK_BARS);
  let hasPullback = false;
  for (let i = pullbackStartIndex; i < latestIndex; i += 1) {
    const barEmaFast = emaFastByBar[i];
    const low = lows[i];
    const close = closes[i];
    if (barEmaFast === undefined || Number.isNaN(barEmaFast)) {
      continue;
    }
    if (low === undefined || close === undefined) {
      continue;
    }

    if (low <= barEmaFast || close < barEmaFast) {
      hasPullback = true;
      break;
    }
  }
  if (!hasPullback) {
    return noSignal(
      'NO_SIGNAL: pullback condition not found',
      'PULLBACK_NOT_FOUND',
      emaFast,
      emaSlow
    );
  }

  const hasReclaim =
    previousClose !== undefined &&
    previousEmaFast !== undefined &&
    !Number.isNaN(previousEmaFast) &&
    entryPrice > emaFast &&
    previousClose <= previousEmaFast;
  if (!hasReclaim) {
    return noSignal(
      'NO_SIGNAL: reclaim condition not found',
      'RECLAIM_NOT_FOUND',
      emaFast,
      emaSlow
    );
  }

  const distanceFromEmaFastPct = ((entryPrice - emaFast) / entryPrice) * 100;
  if (distanceFromEmaFastPct > MAX_DISTANCE_FROM_EMA_FAST_PCT) {
    return noSignal(
      'NO_SIGNAL: entry is too far from EMA fast',
      'CHASE_ENTRY_TOO_FAR_FROM_EMA',
      emaFast,
      emaSlow
    );
  }

  const rsiValue = rsiSeries(closes, RSI_PERIOD).at(-1);
  if (rsiValue === undefined || Number.isNaN(rsiValue)) {
    return noSignal('NO_SIGNAL: RSI is not stable yet', 'RSI_NOT_STABLE', emaFast, emaSlow);
  }
  if (rsiValue < RSI_LOWER_BOUND) {
    return noSignal('NO_SIGNAL: RSI is too low', 'RSI_TOO_LOW', emaFast, emaSlow);
  }
  if (rsiValue > RSI_UPPER_BOUND) {
    return noSignal('NO_SIGNAL: RSI is too high', 'RSI_TOO_HIGH', emaFast, emaSlow);
  }

  const swingLowStop = calculateSwingLow(lows, strategy.swing_low_lookback_bars);
  const stopCandidate = tightenStopForLong(entryPrice, swingLowStop, risk.max_loss_per_trade_pct);
  const latestAtr = atrSeries(highs, lows, closes, ATR_PERIOD).at(-1);
  if (latestAtr !== undefined && Number.isFinite(latestAtr) && latestAtr > 0) {
    const atrStop = entryPrice - latestAtr * ATR_STOP_MULTIPLIER;
    if (atrStop < stopCandidate) {
      return noSignal(
        'NO_SIGNAL: ATR stop conflicts with max loss cap',
        'ATR_STOP_CONFLICT_MAX_LOSS',
        emaFast,
        emaSlow
      );
    }
  }
  const finalStop = stopCandidate;

  if (finalStop >= entryPrice) {
    return noSignal(
      'NO_SIGNAL: stop is not below entry',
      'INVALID_RISK_STRUCTURE',
      emaFast,
      emaSlow
    );
  }

  const stopDistancePct = ((entryPrice - finalStop) / entryPrice) * 100;
  if (stopDistancePct < MIN_STOP_DISTANCE_PCT) {
    return noSignal('NO_SIGNAL: stop is too tight', 'STOP_TOO_TIGHT', emaFast, emaSlow);
  }

  const takeProfitPrice = calculateTakeProfitPrice(
    entryPrice,
    finalStop,
    exit.take_profit_r_multiple
  );

  return entrySignal(
    `ENTER: trend ok + pullback/reclaim, entry=${entryPrice.toFixed(4)}, stop=${finalStop.toFixed(
      4
    )}, tp=${takeProfitPrice.toFixed(4)}, rsi=${rsiValue.toFixed(2)}`,
    emaFast,
    emaSlow,
    entryPrice,
    finalStop,
    takeProfitPrice
  );
}
