import type { Firestore } from '@google-cloud/firestore';
import type { PersistencePort } from '../../app/ports/persistence_port';
import type { BotConfig, Pair, RunRecord, TradeRecord } from '../../domain/model/types';
import type { FirestoreConfigRepository } from '../../infra/config/firestore_config_repo';

interface RepositoryCollections {
  trades: string;
  runs: string;
}

const DEFAULT_COLLECTIONS: RepositoryCollections = {
  trades: 'trades',
  runs: 'runs'
};

function sanitizeFirestoreValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeFirestoreValue(item));
  }

  if (value && typeof value === 'object') {
    const sanitized: Record<string, unknown> = {};
    for (const [key, nestedValue] of Object.entries(value)) {
      if (nestedValue !== undefined) {
        sanitized[key] = sanitizeFirestoreValue(nestedValue);
      }
    }
    return sanitized;
  }

  return value;
}

export class FirestoreRepository implements PersistencePort {
  constructor(
    private readonly firestore: Firestore,
    private readonly configRepo: FirestoreConfigRepository,
    private readonly collections: RepositoryCollections = DEFAULT_COLLECTIONS
  ) {}

  async getCurrentConfig(): Promise<BotConfig> {
    return this.configRepo.getCurrentConfig();
  }

  async createTrade(trade: TradeRecord): Promise<void> {
    await this.firestore
      .collection(this.collections.trades)
      .doc(trade.trade_id)
      .set(sanitizeFirestoreValue(trade) as Record<string, unknown>);
  }

  async updateTrade(tradeId: string, updates: Partial<TradeRecord>): Promise<void> {
    await this.firestore
      .collection(this.collections.trades)
      .doc(tradeId)
      .set(sanitizeFirestoreValue(updates) as Record<string, unknown>, { merge: true });
  }

  async findOpenTrade(pair: Pair): Promise<TradeRecord | null> {
    const snapshot = await this.firestore
      .collection(this.collections.trades)
      .where('state', '==', 'CONFIRMED')
      .get();

    if (snapshot.empty) {
      return null;
    }

    const candidates = snapshot.docs
      .map((doc) => doc.data() as TradeRecord)
      .filter((trade) => trade.pair === pair)
      .sort((a, b) => {
        const aTime = Date.parse(a.created_at);
        const bTime = Date.parse(b.created_at);
        return bTime - aTime;
      });

    return candidates[0] ?? null;
  }

  async countTradesForUtcDay(pair: Pair, dayStartIso: string, dayEndIso: string): Promise<number> {
    const snapshot = await this.firestore
      .collection(this.collections.trades)
      .where('created_at', '>=', dayStartIso)
      .where('created_at', '<=', dayEndIso)
      .get();

    return snapshot.docs
      .map((doc) => doc.data() as TradeRecord)
      .filter((trade) => trade.pair === pair).length;
  }

  async saveRun(run: RunRecord): Promise<void> {
    await this.firestore
      .collection(this.collections.runs)
      .doc(run.run_id)
      .set(sanitizeFirestoreValue(run) as Record<string, unknown>);
  }
}
