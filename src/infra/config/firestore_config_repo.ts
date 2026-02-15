import type { Firestore } from '@google-cloud/firestore';
import type { BotConfig } from '../../domain/model/types';
import { configSchema } from './schema';

export class FirestoreConfigRepository {
  constructor(private readonly firestore: Firestore) {}

  async getCurrentConfig(): Promise<BotConfig> {
    const snapshot = await this.firestore.doc('config/current').get();

    if (!snapshot.exists) {
      throw new Error('config/current document is missing');
    }

    const parsed = configSchema.safeParse(snapshot.data());
    if (!parsed.success) {
      throw new Error(`config/current schema validation failed: ${parsed.error.message}`);
    }

    return parsed.data;
  }
}
