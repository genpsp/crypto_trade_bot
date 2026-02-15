import type { BotConfig, Pair, RunRecord, TradeRecord } from '../../domain/model/types';

export interface PersistencePort {
  getCurrentConfig(): Promise<BotConfig>;
  createTrade(trade: TradeRecord): Promise<void>;
  updateTrade(tradeId: string, updates: Partial<TradeRecord>): Promise<void>;
  findOpenTrade(pair: Pair): Promise<TradeRecord | null>;
  countTradesForUtcDay(pair: Pair, dayStartIso: string, dayEndIso: string): Promise<number>;
  saveRun(run: RunRecord): Promise<void>;
}
