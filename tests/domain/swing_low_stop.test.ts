import { describe, expect, it } from 'vitest';
import {
  calculateMaxLossStopPrice,
  calculateSwingLow,
  calculateTakeProfitPrice,
  tightenStopForLong
} from '../../src/domain/risk/swing_low_stop';

describe('swing low and tp', () => {
  it('calculates swing low from lookback bars', () => {
    const lows = [12, 11, 10.5, 9.8, 10.1, 9.9];
    expect(calculateSwingLow(lows, 4)).toBe(9.8);
  });

  it('calculates take profit with 2R', () => {
    const entry = 100;
    const stop = 95;
    const tp = calculateTakeProfitPrice(entry, stop, 2);
    expect(tp).toBe(110);
  });

  it('calculates max-loss stop as percentage', () => {
    expect(calculateMaxLossStopPrice(100, 0.5)).toBe(99.5);
  });

  it('tightens swing stop toward entry for long position', () => {
    const tightened = tightenStopForLong(100, 92, 0.5);
    expect(tightened).toBe(99.5);
  });

  it('throws when entry is not above stop', () => {
    expect(() => calculateTakeProfitPrice(100, 100, 2)).toThrowError(/entryPrice/);
  });
});
