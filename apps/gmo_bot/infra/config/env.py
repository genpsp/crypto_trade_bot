from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Env:
    REDIS_URL: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    GMO_API_KEY: str
    GMO_API_SECRET: str
    SLACK_WEBHOOK_URL: str | None


REQUIRED_ENV_KEYS = (
    "REDIS_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GMO_API_KEY",
    "GMO_API_SECRET",
)
# Defensive lower bounds to catch obvious typos / placeholder values like "TODO"
# or "fixme". Real GMO secrets are far longer than this threshold.
_MIN_SECRET_LENGTH = 16
_SECRET_CHARSET_PATTERN = re.compile(r"^[A-Za-z0-9._\-+=/]+$")
_SECRET_ENV_KEYS = ("GMO_API_KEY", "GMO_API_SECRET")


def _load_optional_str(source: dict[str, str], key: str) -> str | None:
    raw = source.get(key)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _validate_secret(key: str, value: str) -> None:
    if len(value) < _MIN_SECRET_LENGTH:
        raise RuntimeError(f"{key} looks too short to be a real GMO credential (len={len(value)})")
    if not _SECRET_CHARSET_PATTERN.match(value):
        raise RuntimeError(f"{key} contains unexpected characters; check for whitespace or quoting")


def load_env(source: dict[str, str] | None = None) -> Env:
    env_source = source if source is not None else os.environ  # type: ignore[assignment]
    missing: list[str] = []
    for key in REQUIRED_ENV_KEYS:
        value = env_source.get(key)
        if value is None or value.strip() == "":
            missing.append(key)
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    for key in _SECRET_ENV_KEYS:
        _validate_secret(key, env_source[key].strip())

    return Env(
        REDIS_URL=env_source["REDIS_URL"],
        GOOGLE_APPLICATION_CREDENTIALS=env_source["GOOGLE_APPLICATION_CREDENTIALS"],
        GMO_API_KEY=env_source["GMO_API_KEY"].strip(),
        GMO_API_SECRET=env_source["GMO_API_SECRET"].strip(),
        SLACK_WEBHOOK_URL=_load_optional_str(env_source, "SLACK_WEBHOOK_URL"),
    )
