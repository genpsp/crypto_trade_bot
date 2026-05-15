from __future__ import annotations

from datetime import UTC, datetime
import re
import subprocess


def now_utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def timestamp_token(value: datetime | None = None) -> str:
    current = value or datetime.now(tz=UTC)
    return current.astimezone(UTC).strftime("%Y%m%d-%H%M%S")


def slugify(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return token or "run"


def capture_git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    sha = result.stdout.strip()
    return sha or None


def build_run_id(spec_name: str, *, git_sha: str | None = None, now: datetime | None = None) -> str:
    suffix = (git_sha or "nogit")[:7]
    return f"{timestamp_token(now)}-{slugify(spec_name)}-{suffix}"
