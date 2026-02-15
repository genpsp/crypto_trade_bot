declare module 'node-cron' {
  export interface ScheduleOptions {
    scheduled?: boolean;
    timezone?: string;
  }

  export interface ScheduledTask {
    start(): void;
    stop(): void;
  }

  export function schedule(
    expression: string,
    callback: () => void,
    options?: ScheduleOptions
  ): ScheduledTask;

  const cron: {
    schedule: typeof schedule;
  };

  export default cron;
}
