import type { OhlcvBar, Pair, SignalTimeframe } from '../../domain/model/types';

export interface MarketDataPort {
  fetchBars(pair: Pair, timeframe: SignalTimeframe, limit: number): Promise<OhlcvBar[]>;
}
