import 'dotenv/config';
import { bootstrap } from './infra/bootstrap';

async function main(): Promise<void> {
  const runtime = await bootstrap();

  const shutdown = async (signal: string): Promise<void> => {
    console.log(`[INFO] received ${signal}, shutting down...`);
    await runtime.stop();
    process.exit(0);
  };

  process.on('SIGINT', () => {
    void shutdown('SIGINT');
  });

  process.on('SIGTERM', () => {
    void shutdown('SIGTERM');
  });

  await runtime.start();
}

void main().catch((error: unknown) => {
  console.error('[ERROR] bot startup failed', error);
  process.exit(1);
});
