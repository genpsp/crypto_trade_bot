export interface LockPort {
  acquireRunnerLock(ttlSeconds: number): Promise<boolean>;
  releaseRunnerLock(): Promise<void>;
  hasEntryAttempt(barCloseTimeIso: string): Promise<boolean>;
  markEntryAttempt(barCloseTimeIso: string, ttlSeconds: number): Promise<boolean>;
  setInflightTx(signature: string, ttlSeconds: number): Promise<void>;
  clearInflightTx(signature: string): Promise<void>;
}
