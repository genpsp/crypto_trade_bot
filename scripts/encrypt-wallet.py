from __future__ import annotations

import argparse
import base64
import json
import os
from hashlib import scrypt
from pathlib import Path

import base58
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Encrypt Solana secret key with AES-256-GCM. "
            "Use either --input id.json or --base58 private key."
        )
    )
    parser.add_argument("--input", type=str, help="Path to id.json (64-length number array)")
    parser.add_argument("--base58", type=str, help="Phantom exported base58 private key")
    parser.add_argument("--output", type=str, required=True, help="Output path for encrypted wallet")
    parser.add_argument("--passphrase", type=str, required=True, help="Passphrase")
    args = parser.parse_args()

    if bool(args.input) == bool(args.base58):
        raise ValueError("Provide exactly one of --input or --base58")
    return args


def load_secret_key_from_json(path: str) -> list[int]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, list) or any(not isinstance(value, int) for value in parsed):
        raise ValueError("Wallet input must be a JSON number array")
    if len(parsed) != 64:
        raise ValueError(f"Wallet input length must be 64, got {len(parsed)}")
    return parsed


def load_secret_key_from_base58(secret_key_base58: str) -> list[int]:
    decoded = base58.b58decode(secret_key_base58.strip())
    if len(decoded) == 64:
        return list(decoded)
    if len(decoded) == 32:
        keypair = Keypair.from_seed(decoded)
        return list(bytes(keypair))
    raise ValueError(f"Decoded base58 length must be 32 or 64, got {len(decoded)}")


def encrypt_secret_key(secret_key: list[int], passphrase: str) -> dict[str, str | int]:
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = scrypt(passphrase.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    aes = AESGCM(key)
    plaintext = json.dumps(secret_key).encode("utf-8")
    encrypted = aes.encrypt(iv, plaintext, None)
    ciphertext = encrypted[:-16]
    auth_tag = encrypted[-16:]

    return {
        "version": 1,
        "algorithm": "aes-256-gcm",
        "kdf": "scrypt",
        "salt_base64": base64.b64encode(salt).decode("utf-8"),
        "iv_base64": base64.b64encode(iv).decode("utf-8"),
        "auth_tag_base64": base64.b64encode(auth_tag).decode("utf-8"),
        "ciphertext_base64": base64.b64encode(ciphertext).decode("utf-8"),
    }


def main() -> int:
    args = parse_args()
    if args.input:
        secret_key = load_secret_key_from_json(args.input)
    else:
        secret_key = load_secret_key_from_base58(args.base58)

    encrypted = encrypt_secret_key(secret_key, args.passphrase)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")
    print(f"Encrypted wallet saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

