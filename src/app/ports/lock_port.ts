export interface LockPort {
  acquireRunnerLock(ttlSeconds: number): Promise<boolean>;
  releaseRunnerLock(): Promise<void>;
  markBarProcessed(barCloseTimeIso: string, ttlSeconds: number): Promise<boolean>;
  setInflightTx(signature: string, ttlSeconds: number): Promise<void>;
  clearInflightTx(signature: string): Promise<void>;
}
