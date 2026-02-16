import { ATR, EMA, RSI } from 'technicalindicators';

function isValidPeriod(period: number): boolean {
  return Number.isInteger(period) && period > 0;
}

function isFiniteSeries(values: number[]): boolean {
  return values.every((value) => Number.isFinite(value));
}

export function emaSeries(closes: number[], period: number): number[] {
  if (!isValidPeriod(period) || closes.length === 0 || !isFiniteSeries(closes)) {
    return [];
  }

  try {
    return EMA.calculate({
      period,
      values: closes
    });
  } catch {
    return [];
  }
}

export function rsiSeries(closes: number[], period: number): number[] {
  if (!isValidPeriod(period) || closes.length === 0 || !isFiniteSeries(closes)) {
    return [];
  }

  try {
    return RSI.calculate({
      period,
      values: closes
    });
  } catch {
    return [];
  }
}

export function atrSeries(
  highs: number[],
  lows: number[],
  closes: number[],
  period: number
): number[] {
  if (!isValidPeriod(period)) {
    return [];
  }

  if (highs.length === 0 || lows.length === 0 || closes.length === 0) {
    return [];
  }

  if (highs.length !== lows.length || highs.length !== closes.length) {
    return [];
  }

  if (!isFiniteSeries(highs) || !isFiniteSeries(lows) || !isFiniteSeries(closes)) {
    return [];
  }

  try {
    return ATR.calculate({
      period,
      high: highs,
      low: lows,
      close: closes
    });
  } catch {
    return [];
  }
}
