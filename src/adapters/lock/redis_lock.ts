import { randomUUID } from 'node:crypto';
import type { LockPort } from '../../app/ports/lock_port';
import type { LoggerPort } from '../../app/ports/logger_port';

const RUNNER_LOCK_KEY = 'lock:runner';

interface RedisLike {
  set(...args: unknown[]): Promise<string | null>;
  get(...args: unknown[]): Promise<string | null>;
  del(...args: unknown[]): Promise<number>;
}

export class RedisLockAdapter implements LockPort {
  private runnerLockToken: string | null = null;

  constructor(
    private readonly redis: RedisLike,
    private readonly logger: LoggerPort
  ) {}

  async acquireRunnerLock(ttlSeconds: number): Promise<boolean> {
    const token = randomUUID();
    const result = await this.redis.set(RUNNER_LOCK_KEY, token, {
      NX: true,
      EX: ttlSeconds
    });

    if (result === 'OK') {
      this.runnerLockToken = token;
      return true;
    }

    return false;
  }

  async releaseRunnerLock(): Promise<void> {
    if (!this.runnerLockToken) {
      return;
    }

    const currentToken = await this.redis.get(RUNNER_LOCK_KEY);
    if (currentToken === this.runnerLockToken) {
      await this.redis.del(RUNNER_LOCK_KEY);
    } else {
      this.logger.warn('Runner lock token mismatch on release');
    }

    this.runnerLockToken = null;
  }

  async markBarProcessed(barCloseTimeIso: string, ttlSeconds: number): Promise<boolean> {
    const key = `idem:signal:${barCloseTimeIso}`;
    const result = await this.redis.set(key, '1', {
      NX: true,
      EX: ttlSeconds
    });

    return result === 'OK';
  }

  async setInflightTx(signature: string, ttlSeconds: number): Promise<void> {
    await this.redis.set(`tx:inflight:${signature}`, '1', {
      EX: ttlSeconds
    });
  }

  async clearInflightTx(signature: string): Promise<void> {
    await this.redis.del(`tx:inflight:${signature}`);
  }
}
