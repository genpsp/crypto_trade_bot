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

from pybot.adapters.execution.http_retry import RETRIABLE_HTTP_STATUS_CODES, retry_delay_seconds
from pybot.app.ports.logger_port import LoggerPort

RPC_RETRY_ATTEMPTS = 4
RPC_RETRY_BASE_DELAY_SECONDS = 0.35
RPC_HTTP_TIMEOUT_SECONDS = 8
RETRIABLE_RPC_ERROR_CODES = {-32005, -32004, -32603}
RETRIABLE_RPC_ERROR_MARKERS = (
    "too many requests",
    "rate limit",
    "temporarily unavailable",
    "node is behind",
    "timed out",
    "timeout",
    "service unavailable",
)


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


def _extract_rpc_error_text(error_obj: Any) -> str:
    if isinstance(error_obj, dict):
        message = error_obj.get("message")
        if isinstance(message, str):
            return message
    return str(error_obj)


def _is_retriable_rpc_error(error_obj: Any) -> bool:
    if isinstance(error_obj, dict):
        code = error_obj.get("code")
        if isinstance(code, int) and code in RETRIABLE_RPC_ERROR_CODES:
            return True
    message = _extract_rpc_error_text(error_obj).lower()
    return any(marker in message for marker in RETRIABLE_RPC_ERROR_MARKERS)


class SolanaSender:
    def __init__(self, rpc_url: str, wallet_key_path: str, wallet_passphrase: str, logger: LoggerPort):
        self.rpc_url = rpc_url
        self.logger = logger
        self.keypair = Keypair.from_bytes(_decrypt_secret_key(wallet_key_path, wallet_passphrase))

    def get_public_key_base58(self) -> str:
        return str(self.keypair.pubkey())

    def get_spl_token_balance_ui_amount(self, mint: str) -> float:
        owner = self.get_public_key_base58()
        result = self._rpc(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
        )
        if not isinstance(result, dict):
            return 0.0
        value = result.get("value")
        if not isinstance(value, list):
            return 0.0

        total_ui_amount = 0.0
        for account in value:
            if not isinstance(account, dict):
                continue
            account_obj = account.get("account")
            if not isinstance(account_obj, dict):
                continue
            data = account_obj.get("data")
            if not isinstance(data, dict):
                continue
            parsed = data.get("parsed")
            if not isinstance(parsed, dict):
                continue
            info = parsed.get("info")
            if not isinstance(info, dict):
                continue
            token_amount = info.get("tokenAmount")
            if not isinstance(token_amount, dict):
                continue

            ui_amount = token_amount.get("uiAmount")
            if isinstance(ui_amount, (int, float)):
                total_ui_amount += float(ui_amount)
                continue

            raw_amount = token_amount.get("amount")
            decimals = token_amount.get("decimals")
            if isinstance(raw_amount, str) and isinstance(decimals, int) and decimals >= 0:
                try:
                    total_ui_amount += int(raw_amount) / (10**decimals)
                except Exception:
                    continue

        return total_ui_amount

    def get_native_sol_balance_ui_amount(self) -> float:
        owner = self.get_public_key_base58()
        result = self._rpc("getBalance", [owner, {"commitment": "confirmed"}])
        if not isinstance(result, dict):
            return 0.0
        value = result.get("value")
        if not isinstance(value, int) or value < 0:
            return 0.0
        lamports_per_sol = 1_000_000_000
        return value / lamports_per_sol

    def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        for attempt in range(1, RPC_RETRY_ATTEMPTS + 1):
            try:
                response = requests.post(self.rpc_url, json=payload, timeout=RPC_HTTP_TIMEOUT_SECONDS)
            except requests.RequestException as error:
                if attempt < RPC_RETRY_ATTEMPTS:
                    time.sleep(retry_delay_seconds(RPC_RETRY_BASE_DELAY_SECONDS, attempt))
                    continue
                raise RuntimeError(f"RPC {method} failed: {error}") from error

            if response.status_code != 200:
                should_retry = (
                    response.status_code in RETRIABLE_HTTP_STATUS_CODES and attempt < RPC_RETRY_ATTEMPTS
                )
                if should_retry:
                    time.sleep(retry_delay_seconds(RPC_RETRY_BASE_DELAY_SECONDS, attempt))
                    continue
                response.raise_for_status()

            try:
                data = response.json()
            except ValueError as error:
                if attempt < RPC_RETRY_ATTEMPTS:
                    time.sleep(retry_delay_seconds(RPC_RETRY_BASE_DELAY_SECONDS, attempt))
                    continue
                raise RuntimeError(f"RPC {method} returned invalid JSON: {error}") from error

            if "error" in data:
                rpc_error = data["error"]
                if attempt < RPC_RETRY_ATTEMPTS and _is_retriable_rpc_error(rpc_error):
                    time.sleep(retry_delay_seconds(RPC_RETRY_BASE_DELAY_SECONDS, attempt))
                    continue
                raise RuntimeError(f"RPC {method} failed: {rpc_error}")

            return data.get("result")

        raise RuntimeError(f"RPC {method} failed: retry attempts exhausted")

    def send_versioned_transaction_base64(self, serialized_base64: str) -> str:
        tx_bytes = base64.b64decode(serialized_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signature = self.keypair.sign_message(to_bytes_versioned(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, [signature])
        wire_base64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")

        # Solana JSON-RPC standard method is sendTransaction.
        result = self._rpc(
            "sendTransaction",
            [wire_base64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        )
        if not isinstance(result, str):
            raise RuntimeError("sendTransaction result is invalid")

        self.logger.info("Transaction submitted", {"signature": result})
        return result

    def confirm_signature(
        self, signature: str, timeout_ms: int, poll_interval_ms: int = 1000
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

    def get_transaction_fee_lamports(self, signature: str) -> int | None:
        result = self._rpc(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if not isinstance(result, dict):
            return None
        meta = result.get("meta")
        if not isinstance(meta, dict):
            return None
        fee = meta.get("fee")
        if isinstance(fee, int) and fee >= 0:
            return fee
        return None
