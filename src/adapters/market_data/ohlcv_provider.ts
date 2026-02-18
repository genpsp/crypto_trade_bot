import type { MarketDataPort } from '../../app/ports/market_data_port';
import type { OhlcvBar, Pair, SignalTimeframe } from '../../domain/model/types';
import { getBarDurationMs } from '../../domain/utils/time';

const BINANCE_KLINES_URL = 'https://api.binance.com/api/v3/klines';

const PAIR_SYMBOL_MAP: Record<Pair, string> = {
  'SOL/USDC': 'SOLUSDC'
};

const TIMEFRAME_TO_BINANCE_INTERVAL: Record<SignalTimeframe, string> = {
  '2h': '2h',
  '4h': '4h'
};

export class OhlcvProvider implements MarketDataPort {
  async fetchBars(pair: Pair, timeframe: SignalTimeframe, limit: number): Promise<OhlcvBar[]> {
    if (limit <= 0 || limit > 1000) {
      throw new Error(`OHLCV limit must be 1..1000, got ${limit}`);
    }

    const symbol = PAIR_SYMBOL_MAP[pair];
    const interval = TIMEFRAME_TO_BINANCE_INTERVAL[timeframe];
    const barDurationMs = getBarDurationMs(timeframe);
    const url = new URL(BINANCE_KLINES_URL);
    url.searchParams.set('symbol', symbol);
    url.searchParams.set('interval', interval);
    url.searchParams.set('limit', String(limit));

    const response = await fetch(url.toString());
    if (!response.ok) {
      throw new Error(`Failed to fetch OHLCV: HTTP ${response.status}`);
    }

    const payload = (await response.json()) as unknown;
    if (!Array.isArray(payload)) {
      throw new Error('OHLCV payload is not an array');
    }

    return payload.map((row: unknown, index: number) => {
      if (!Array.isArray(row) || row.length < 6) {
        throw new Error(`Invalid OHLCV row at index ${index}`);
      }

      const openTimeMs = Number(row[0]);
      const openTime = new Date(openTimeMs);

      return {
        openTime,
        closeTime: new Date(openTimeMs + barDurationMs),
        open: Number(row[1]),
        high: Number(row[2]),
        low: Number(row[3]),
        close: Number(row[4]),
        volume: Number(row[5])
      };
    });
  }
}
