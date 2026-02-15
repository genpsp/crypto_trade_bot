export interface Env {
  SOLANA_RPC_URL: string;
  REDIS_URL: string;
  GOOGLE_APPLICATION_CREDENTIALS: string;
  WALLET_KEY_PATH: string;
  WALLET_KEY_PASSPHRASE: string;
}

const REQUIRED_ENV_KEYS: Array<keyof Env> = [
  'SOLANA_RPC_URL',
  'REDIS_URL',
  'GOOGLE_APPLICATION_CREDENTIALS',
  'WALLET_KEY_PATH',
  'WALLET_KEY_PASSPHRASE'
];

export function loadEnv(source: NodeJS.ProcessEnv = process.env): Env {
  const missing = REQUIRED_ENV_KEYS.filter((key) => {
    const value = source[key];
    return value === undefined || value.trim() === '';
  });

  if (missing.length > 0) {
    throw new Error(`Missing required env vars: ${missing.join(', ')}`);
  }

  return {
    SOLANA_RPC_URL: source.SOLANA_RPC_URL as string,
    REDIS_URL: source.REDIS_URL as string,
    GOOGLE_APPLICATION_CREDENTIALS: source.GOOGLE_APPLICATION_CREDENTIALS as string,
    WALLET_KEY_PATH: source.WALLET_KEY_PATH as string,
    WALLET_KEY_PASSPHRASE: source.WALLET_KEY_PASSPHRASE as string
  };
}
