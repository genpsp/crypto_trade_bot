import { Firestore } from '@google-cloud/firestore';
import { createClient } from 'redis';
import type { ExecutionPort } from '../app/ports/execution_port';
import { runCycle as runCycleUsecase } from '../app/usecases/run_cycle';
import { JupiterQuoteClient } from '../adapters/execution/jupiter_quote_client';
import { JupiterSwapAdapter } from '../adapters/execution/jupiter_swap';
import { PaperExecutionAdapter } from '../adapters/execution/paper_execution';
import { SolanaSender } from '../adapters/execution/solana_sender';
import { RedisLockAdapter } from '../adapters/lock/redis_lock';
import { OhlcvProvider } from '../adapters/market_data/ohlcv_provider';
import { FirestoreRepository } from '../adapters/persistence/firestore_repo';
import { FirestoreConfigRepository } from './config/firestore_config_repo';
import { loadEnv } from './config/env';
import { createLogger } from './logging/logger';
import { createCronCycle, type CronController } from './scheduler/cron_cycle';

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
  const startupConfig = await configRepo.getCurrentConfig();
  const mode = startupConfig.execution.mode;
  const collections =
    mode === 'PAPER'
      ? {
          trades: 'paper_trades',
          runs: 'paper_runs'
        }
      : {
          trades: 'trades',
          runs: 'runs'
        };

  const persistence = new FirestoreRepository(firestore, configRepo, collections);
  const lock = new RedisLockAdapter(redis, logger);
  const marketData = new OhlcvProvider();
  const quoteClient = new JupiterQuoteClient();

  let execution: ExecutionPort;
  if (mode === 'PAPER') {
    execution = new PaperExecutionAdapter(quoteClient, logger);
  } else {
    const sender = new SolanaSender(
      env.SOLANA_RPC_URL,
      env.WALLET_KEY_PATH,
      env.WALLET_KEY_PASSPHRASE,
      logger
    );
    execution = new JupiterSwapAdapter(quoteClient, sender, logger);
  }

  logger.info('runtime mode selected', {
    mode,
    trades_collection: collections.trades,
    runs_collection: collections.runs
  });

  const shouldSuppressRunCycleLog = (result: {
    result: string;
    summary: string;
  }): boolean =>
    result.result === 'SKIPPED_ENTRY' &&
    (result.summary === 'SKIPPED_ENTRY: entry already evaluated for this bar' ||
      result.summary === 'SKIPPED_ENTRY: idem entry key already exists for this bar');

  const executeCycle = async (): Promise<void> => {
    const result = await runCycleUsecase({
      execution,
      lock,
      logger,
      marketData,
      persistence
    });

    if (shouldSuppressRunCycleLog(result)) {
      return;
    }

    logger.info('run_cycle finished', {
      run_id: result.run_id,
      result: result.result,
      summary: result.summary,
      trade_id: result.trade_id
    });
  };

  const executeScheduledCycle = async (): Promise<void> => {
    const now = new Date();
    const isFiveMinuteWindow = now.getUTCMinutes() % 5 === 0;
    if (!isFiveMinuteWindow) {
      const openTrade = await persistence.findOpenTrade(startupConfig.pair);
      if (!openTrade) {
        return;
      }
    }

    await executeCycle();
  };

  let scheduler: CronController | null = null;

  return {
    async start() {
      logger.info('bot startup: run first cycle immediately');
      await executeCycle();

      scheduler = createCronCycle(executeScheduledCycle, logger);
      scheduler.start();
    },
    async stop() {
      scheduler?.stop();
      await redis.quit();
      logger.info('bot stopped');
    }
  };
}
