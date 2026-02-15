import { createDecipheriv, scryptSync } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  Connection,
  Keypair,
  VersionedTransaction,
  type Commitment,
  type PublicKey
} from '@solana/web3.js';
import type { LoggerPort } from '../../app/ports/logger_port';

interface EncryptedWalletFile {
  version: number;
  algorithm: 'aes-256-gcm';
  kdf: 'scrypt';
  salt_base64: string;
  iv_base64: string;
  auth_tag_base64: string;
  ciphertext_base64: string;
}

export interface SignatureConfirmation {
  confirmed: boolean;
  error?: string;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function parseEncryptedWalletFile(path: string): EncryptedWalletFile {
  const raw = readFileSync(path, 'utf8');
  const parsed = JSON.parse(raw) as Partial<EncryptedWalletFile>;

  if (
    parsed.version !== 1 ||
    parsed.algorithm !== 'aes-256-gcm' ||
    parsed.kdf !== 'scrypt' ||
    !parsed.salt_base64 ||
    !parsed.iv_base64 ||
    !parsed.auth_tag_base64 ||
    !parsed.ciphertext_base64
  ) {
    throw new Error('Invalid encrypted wallet file format');
  }

  return parsed as EncryptedWalletFile;
}

function decryptSecretKey(path: string, passphrase: string): Uint8Array {
  const encrypted = parseEncryptedWalletFile(path);

  const salt = Buffer.from(encrypted.salt_base64, 'base64');
  const iv = Buffer.from(encrypted.iv_base64, 'base64');
  const authTag = Buffer.from(encrypted.auth_tag_base64, 'base64');
  const ciphertext = Buffer.from(encrypted.ciphertext_base64, 'base64');

  const key = scryptSync(passphrase, salt, 32);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(authTag);

  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  const secretArray = JSON.parse(plaintext.toString('utf8')) as unknown;

  if (!Array.isArray(secretArray) || secretArray.some((value) => typeof value !== 'number')) {
    throw new Error('Decrypted wallet payload must be a number array');
  }

  if (secretArray.length !== 64) {
    throw new Error(`Decrypted secret key length must be 64, got ${secretArray.length}`);
  }

  return Uint8Array.from(secretArray);
}

export class SolanaSender {
  private readonly connection: Connection;

  private readonly keypair: Keypair;

  constructor(
    rpcUrl: string,
    walletKeyPath: string,
    walletPassphrase: string,
    private readonly logger: LoggerPort,
    commitment: Commitment = 'confirmed'
  ) {
    this.connection = new Connection(rpcUrl, commitment);
    this.keypair = Keypair.fromSecretKey(decryptSecretKey(walletKeyPath, walletPassphrase));
  }

  getPublicKey(): PublicKey {
    return this.keypair.publicKey;
  }

  async sendVersionedTransactionBase64(serializedBase64: string): Promise<string> {
    const txBytes = Buffer.from(serializedBase64, 'base64');
    const tx = VersionedTransaction.deserialize(txBytes);
    tx.sign([this.keypair]);

    const signature = await this.connection.sendRawTransaction(tx.serialize(), {
      skipPreflight: false,
      maxRetries: 3
    });

    this.logger.info('Transaction submitted', { signature });
    return signature;
  }

  async confirmSignature(
    signature: string,
    timeoutMs: number,
    pollIntervalMs = 2000
  ): Promise<SignatureConfirmation> {
    const startedAt = Date.now();

    while (Date.now() - startedAt <= timeoutMs) {
      const statuses = await this.connection.getSignatureStatuses([signature], {
        searchTransactionHistory: true
      });
      const status = statuses.value[0];

      if (status?.err) {
        return {
          confirmed: false,
          error: JSON.stringify(status.err)
        };
      }

      if (status?.confirmationStatus === 'confirmed' || status?.confirmationStatus === 'finalized') {
        return {
          confirmed: true
        };
      }

      await sleep(pollIntervalMs);
    }

    return {
      confirmed: false,
      error: `confirmation timeout after ${timeoutMs}ms`
    };
  }
}
