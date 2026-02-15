import type { OhlcvBar, Pair } from '../../domain/model/types';

export interface MarketDataPort {
  fetch4hBars(pair: Pair, limit: number): Promise<OhlcvBar[]>;
}
