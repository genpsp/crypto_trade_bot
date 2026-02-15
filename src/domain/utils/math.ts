export function roundTo(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

export function percentOf(value: number, pct: number): number {
  return (value * pct) / 100;
}
