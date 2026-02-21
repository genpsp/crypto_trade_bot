from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from hashlib import scrypt
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from pybot.app.ports.logger_port import LoggerPort


@dataclass
class SignatureConfirmation:
    confirmed: bool
    error: str | None = None


def _parse_encrypted_wallet_file(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(raw)
    required_keys = {
        "version",
        "algorithm",
        "kdf",
        "salt_base64",
        "iv_base64",
        "auth_tag_base64",
        "ciphertext_base64",
    }
    if (
        parsed.get("version") != 1
        or parsed.get("algorithm") != "aes-256-gcm"
        or parsed.get("kdf") != "scrypt"
        or not required_keys.issubset(parsed.keys())
    ):
        raise ValueError("Invalid encrypted wallet file format")
    return parsed


def _decrypt_secret_key(path: str, passphrase: str) -> bytes:
    encrypted = _parse_encrypted_wallet_file(path)
    salt = base64.b64decode(encrypted["salt_base64"])
    iv = base64.b64decode(encrypted["iv_base64"])
    auth_tag = base64.b64decode(encrypted["auth_tag_base64"])
    ciphertext = base64.b64decode(encrypted["ciphertext_base64"])
    key = scrypt(passphrase.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)

    aes_gcm = AESGCM(key)
    plaintext = aes_gcm.decrypt(iv, ciphertext + auth_tag, None)
    secret_array = json.loads(plaintext.decode("utf-8"))
    if not isinstance(secret_array, list) or any(not isinstance(item, int) for item in secret_array):
        raise ValueError("Decrypted wallet payload must be a number array")
    if len(secret_array) != 64:
        raise ValueError(f"Decrypted secret key length must be 64, got {len(secret_array)}")
    return bytes(secret_array)


class SolanaSender:
    def __init__(self, rpc_url: str, wallet_key_path: str, wallet_passphrase: str, logger: LoggerPort):
        self.rpc_url = rpc_url
        self.logger = logger
        self.keypair = Keypair.from_bytes(_decrypt_secret_key(wallet_key_path, wallet_passphrase))

    def get_public_key_base58(self) -> str:
        return str(self.keypair.pubkey())

    def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"RPC {method} failed: {data['error']}")
        return data.get("result")

    def send_versioned_transaction_base64(self, serialized_base64: str) -> str:
        tx_bytes = base64.b64decode(serialized_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signature = self.keypair.sign_message(to_bytes_versioned(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, [signature])
        wire_base64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")

        result = self._rpc(
            "sendRawTransaction",
            [wire_base64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        )
        if not isinstance(result, str):
            raise RuntimeError("sendRawTransaction result is invalid")

        self.logger.info("Transaction submitted", {"signature": result})
        return result

    def confirm_signature(
        self, signature: str, timeout_ms: int, poll_interval_ms: int = 2000
    ) -> SignatureConfirmation:
        started_at = int(time.time() * 1000)
        while int(time.time() * 1000) - started_at <= timeout_ms:
            result = self._rpc(
                "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
            )
            status = None
            if isinstance(result, dict):
                values = result.get("value")
                if isinstance(values, list) and values:
                    status = values[0]

            if isinstance(status, dict):
                if status.get("err") is not None:
                    return SignatureConfirmation(confirmed=False, error=json.dumps(status.get("err")))
                confirmation_status = status.get("confirmationStatus")
                if confirmation_status in ("confirmed", "finalized"):
                    return SignatureConfirmation(confirmed=True)

            time.sleep(poll_interval_ms / 1000)

        return SignatureConfirmation(
            confirmed=False,
            error=f"confirmation timeout after {timeout_ms}ms",
        )

