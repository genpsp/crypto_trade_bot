import { Firestore } from '@google-cloud/firestore';
import { createClient } from 'redis';
import { run4hCycle } from '../app/usecases/run_4h_cycle';
import { JupiterSwapAdapter } from '../adapters/execution/jupiter_swap';
import { SolanaSender } from '../adapters/execution/solana_sender';
import { RedisLockAdapter } from '../adapters/lock/redis_lock';
import { OhlcvProvider } from '../adapters/market_data/ohlcv_provider';
import { FirestoreRepository } from '../adapters/persistence/firestore_repo';
import { FirestoreConfigRepository } from './config/firestore_config_repo';
import { loadEnv } from './config/env';
import { createLogger } from './logging/logger';
import { createCron4h, type CronController } from './scheduler/cron_4h';

export interface AppRuntime {
  start(): Promise<void>;
  stop(): Promise<void>;
}

export async function bootstrap(): Promise<AppRuntime> {
  const env = loadEnv();
  const logger = createLogger('bot');

  const firestore = new Firestore({
    keyFilename: env.GOOGLE_APPLICATION_CREDENTIALS
  });

  const redis = createClient({
    url: env.REDIS_URL
  });

  redis.on('error', (error: unknown) => {
    logger.error('Redis client error', {
      error: error instanceof Error ? error.message : String(error)
    });
  });

  await redis.connect();

  const configRepo = new FirestoreConfigRepository(firestore);
  const persistence = new FirestoreRepository(firestore, configRepo);
  const lock = new RedisLockAdapter(redis, logger);
  const marketData = new OhlcvProvider();
  const sender = new SolanaSender(
    env.SOLANA_RPC_URL,
    env.WALLET_KEY_PATH,
    env.WALLET_KEY_PASSPHRASE,
    logger
  );
  const execution = new JupiterSwapAdapter(sender, logger);

  const runCycle = async (): Promise<void> => {
    const result = await run4hCycle({
      execution,
      lock,
      logger,
      marketData,
      persistence
    });

    logger.info('run_4h_cycle finished', {
      run_id: result.run_id,
      result: result.result,
      summary: result.summary,
      trade_id: result.trade_id
    });
  };

  let scheduler: CronController | null = null;

  return {
    async start() {
      logger.info('bot startup: run first cycle immediately');
      await runCycle();

      scheduler = createCron4h(runCycle, logger);
      scheduler.start();
    },
    async stop() {
      scheduler?.stop();
      await redis.quit();
      logger.info('bot stopped');
    }
  };
}
