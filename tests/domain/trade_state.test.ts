import { describe, expect, it } from 'vitest';
import {
  assertTradeStateTransition,
  canTransitionTradeState
} from '../../src/domain/model/trade_state';

describe('trade state transitions', () => {
  it('allows valid transitions', () => {
    expect(canTransitionTradeState('CREATED', 'SUBMITTED')).toBe(true);
    expect(canTransitionTradeState('SUBMITTED', 'CONFIRMED')).toBe(true);
    expect(canTransitionTradeState('CONFIRMED', 'CLOSED')).toBe(true);
    expect(canTransitionTradeState('CONFIRMED', 'SUBMITTED')).toBe(true);
    expect(canTransitionTradeState('SUBMITTED', 'FAILED')).toBe(true);
  });

  it('rejects invalid transitions', () => {
    expect(canTransitionTradeState('CREATED', 'CLOSED')).toBe(false);
    expect(() => assertTradeStateTransition('CREATED', 'CLOSED')).toThrowError(
      /Invalid trade state transition/
    );
  });
});
