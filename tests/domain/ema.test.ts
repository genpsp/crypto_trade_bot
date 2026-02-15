import { describe, expect, it } from 'vitest';
import { calculateEmaSeries } from '../../src/domain/indicators/ema';

describe('calculateEmaSeries', () => {
  it('returns a series with same length and finite latest value', () => {
    const closes = Array.from({ length: 120 }, (_, i) => 100 + i * 0.5);
    const ema = calculateEmaSeries(closes, 20);

    expect(ema).toHaveLength(closes.length);
    expect(Number.isFinite(ema.at(-1))).toBe(true);
  });

  it('throws for invalid period', () => {
    expect(() => calculateEmaSeries([1, 2, 3], 0)).toThrowError(/greater than 0/);
  });
});
