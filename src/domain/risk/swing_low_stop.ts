export function calculateSwingLow(lows: number[], lookbackBars: number): number {
  if (lookbackBars <= 0) {
    throw new Error('lookbackBars must be greater than 0');
  }

  if (lows.length < lookbackBars) {
    throw new Error(
      `Not enough lows for swing low: required=${lookbackBars}, actual=${lows.length}`
    );
  }

  const recentLows = lows.slice(-lookbackBars);
  return Math.min(...recentLows);
}

export function calculateMaxLossStopPrice(entryPrice: number, maxLossPct: number): number {
  if (entryPrice <= 0) {
    throw new Error('entryPrice must be greater than 0');
  }

  if (maxLossPct <= 0) {
    throw new Error('maxLossPct must be greater than 0');
  }

  const maxLossRatio = maxLossPct / 100;
  return entryPrice * (1 - maxLossRatio);
}

export function tightenStopForLong(
  entryPrice: number,
  swingLowStop: number,
  maxLossPct: number
): number {
  const pctStop = calculateMaxLossStopPrice(entryPrice, maxLossPct);
  return Math.max(swingLowStop, pctStop);
}

export function calculateTakeProfitPrice(
  entryPrice: number,
  stopPrice: number,
  rMultiple: number
): number {
  if (rMultiple <= 0) {
    throw new Error('rMultiple must be greater than 0');
  }

  if (entryPrice <= stopPrice) {
    throw new Error('entryPrice must be greater than stopPrice');
  }

  const oneR = entryPrice - stopPrice;
  return entryPrice + oneR * rMultiple;
}
