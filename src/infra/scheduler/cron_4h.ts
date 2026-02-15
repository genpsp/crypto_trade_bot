import cron, { type ScheduledTask } from 'node-cron';
import type { LoggerPort } from '../../app/ports/logger_port';

export interface CronController {
  start(): void;
  stop(): void;
}

export function createCron4h(task: () => Promise<void>, logger: LoggerPort): CronController {
  const schedule = '*/5 * * * *';

  const cronTask: ScheduledTask = cron.schedule(
    schedule,
    () => {
      void task().catch((error: unknown) => {
        logger.error('cron task failed', {
          error: error instanceof Error ? error.message : String(error)
        });
      });
    },
    {
      scheduled: false,
      timezone: 'UTC'
    }
  );

  return {
    start() {
      logger.info('scheduler started (every 5 minutes, UTC)', { schedule });
      cronTask.start();
    },
    stop() {
      logger.info('scheduler stopped');
      cronTask.stop();
    }
  };
}
