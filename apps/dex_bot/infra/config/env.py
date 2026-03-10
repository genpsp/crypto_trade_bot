from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Env:
    SOLANA_RPC_URL: str
    REDIS_URL: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    WALLET_KEY_PASSPHRASE: str
    SLACK_WEBHOOK_URL: str | None


REQUIRED_ENV_KEYS = (
    "SOLANA_RPC_URL",
    "REDIS_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "WALLET_KEY_PASSPHRASE",
)

def _load_optional_str(source: dict[str, str], key: str) -> str | None:
    raw = source.get(key)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def load_env(source: dict[str, str] | None = None) -> Env:
    env_source = source if source is not None else os.environ  # type: ignore[assignment]
    missing = []
    for key in REQUIRED_ENV_KEYS:
        value = env_source.get(key)
        if value is None or value.strip() == "":
            missing.append(key)
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    return Env(
        SOLANA_RPC_URL=env_source["SOLANA_RPC_URL"],
        REDIS_URL=env_source["REDIS_URL"],
        GOOGLE_APPLICATION_CREDENTIALS=env_source["GOOGLE_APPLICATION_CREDENTIALS"],
        WALLET_KEY_PASSPHRASE=env_source["WALLET_KEY_PASSPHRASE"],
        SLACK_WEBHOOK_URL=_load_optional_str(env_source, "SLACK_WEBHOOK_URL"),
    )
