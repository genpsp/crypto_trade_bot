import { EMA } from 'trading-signals';

export function calculateEmaSeries(values: number[], period: number): number[] {
  if (period <= 0) {
    throw new Error('EMA period must be greater than 0');
  }

  if (values.length === 0) {
    return [];
  }

  const indicator = new EMA(period);

  return values.map((value) => {
    indicator.update(value);
    return indicator.getResult() ?? Number.NaN;
  });
}

export function getLatestEma(values: number[], period: number): number {
  const series = calculateEmaSeries(values, period);
  const latest = series.at(-1);

  if (latest === undefined || Number.isNaN(latest)) {
    throw new Error('EMA is not stable with current data');
  }

  return latest;
}
