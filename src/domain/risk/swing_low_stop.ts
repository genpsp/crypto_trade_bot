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
