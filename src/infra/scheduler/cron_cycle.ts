import cron, { type ScheduledTask } from 'node-cron';
import type { LoggerPort } from '../../app/ports/logger_port';

export interface CronController {
  start(): void;
  stop(): void;
}

export function createCronCycle(task: () => Promise<void>, logger: LoggerPort): CronController {
  const schedule = '* * * * *';

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
      logger.info('scheduler started (every 1 minute, UTC)', { schedule });
      cronTask.start();
    },
    stop() {
      logger.info('scheduler stopped');
      cronTask.stop();
    }
  };
}
