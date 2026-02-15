import { Keypair } from '@solana/web3.js';
import bs58 from 'bs58';
import { createCipheriv, randomBytes, scryptSync } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';

interface CliArgs {
  input?: string;
  base58?: string;
  output: string;
  passphrase: string;
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'number');
}

function parseArgs(argv: string[]): CliArgs {
  const argMap = new Map<string, string>();

  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];

    if (!key?.startsWith('--') || !value) {
      continue;
    }

    argMap.set(key, value);
  }

  const input = argMap.get('--input');
  const base58 = argMap.get('--base58');
  const output = argMap.get('--output');
  const passphrase = argMap.get('--passphrase');

  if ((!input && !base58) || !output || !passphrase) {
    throw new Error(
      'Usage: npm run encrypt-wallet -- (--input /path/id.json | --base58 "<phantom-private-key>") --output /path/wallet.enc.json --passphrase "..."'
    );
  }

  if (input && base58) {
    throw new Error('Provide either --input or --base58, not both');
  }

  return {
    input,
    base58,
    output,
    passphrase
  };
}

function loadSecretKeyFromJson(path: string): number[] {
  const raw = readFileSync(path, 'utf8');
  const parsedUnknown: unknown = JSON.parse(raw);

  if (!isNumberArray(parsedUnknown)) {
    throw new Error('Wallet input must be a JSON number array');
  }

  if (parsedUnknown.length !== 64) {
    throw new Error(`Wallet input length must be 64, got ${parsedUnknown.length}`);
  }

  return parsedUnknown;
}

function loadSecretKeyFromBase58(secretKeyBase58: string): number[] {
  const decodedUnknown: unknown = bs58.decode(secretKeyBase58.trim());
  if (!(decodedUnknown instanceof Uint8Array)) {
    throw new Error('Decoded base58 secret key is not Uint8Array');
  }

  const decoded = decodedUnknown;

  if (decoded.length === 64) {
    return Array.from(decoded);
  }

  if (decoded.length === 32) {
    const keypair = Keypair.fromSeed(decoded);
    return Array.from(keypair.secretKey);
  }

  throw new Error(`Decoded base58 length must be 32 or 64, got ${decoded.length}`);
}

function loadSecretKey(args: CliArgs): number[] {
  if (args.input) {
    return loadSecretKeyFromJson(args.input);
  }

  if (args.base58) {
    return loadSecretKeyFromBase58(args.base58);
  }

  throw new Error('Either --input or --base58 is required');
}

function encryptSecretKey(secretKey: number[], passphrase: string): Record<string, unknown> {
  const salt = randomBytes(16);
  const iv = randomBytes(12);
  const key = scryptSync(passphrase, salt, 32);

  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const plaintext = Buffer.from(JSON.stringify(secretKey), 'utf8');
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const authTag = cipher.getAuthTag();

  return {
    version: 1,
    algorithm: 'aes-256-gcm',
    kdf: 'scrypt',
    salt_base64: salt.toString('base64'),
    iv_base64: iv.toString('base64'),
    auth_tag_base64: authTag.toString('base64'),
    ciphertext_base64: ciphertext.toString('base64')
  };
}

function main(): void {
  const args = parseArgs(process.argv.slice(2));
  const secretKey = loadSecretKey(args);
  const encrypted = encryptSecretKey(secretKey, args.passphrase);

  writeFileSync(args.output, JSON.stringify(encrypted, null, 2), 'utf8');
  console.log(`Encrypted wallet saved to ${args.output}`);
}

main();
