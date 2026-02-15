export const TRADE_STATE_VALUES = [
  'CREATED',
  'SUBMITTED',
  'CONFIRMED',
  'CLOSED',
  'FAILED',
  'CANCELED'
] as const;

export type TradeState = (typeof TRADE_STATE_VALUES)[number];

const ALLOWED_TRANSITIONS: Record<TradeState, readonly TradeState[]> = {
  CREATED: ['SUBMITTED', 'FAILED', 'CANCELED'],
  SUBMITTED: ['CONFIRMED', 'FAILED', 'CANCELED'],
  CONFIRMED: ['SUBMITTED', 'CLOSED', 'FAILED', 'CANCELED'],
  CLOSED: [],
  FAILED: [],
  CANCELED: []
};

export function canTransitionTradeState(from: TradeState, to: TradeState): boolean {
  return ALLOWED_TRANSITIONS[from].includes(to);
}

export function assertTradeStateTransition(from: TradeState, to: TradeState): void {
  if (!canTransitionTradeState(from, to)) {
    throw new Error(`Invalid trade state transition: ${from} -> ${to}`);
  }
}
