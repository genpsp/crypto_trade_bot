from __future__ import annotations

from uuid import uuid4

from redis import Redis

from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort

RUNNER_LOCK_KEY_PREFIX = "lock:runner"


class RedisLockAdapter(LockPort):
    def __init__(self, redis: Redis, logger: LoggerPort, lock_namespace: str):
        self.redis = redis
        self.logger = logger
        self.lock_namespace = lock_namespace
        self.runner_lock_token: str | None = None

    def _runner_lock_key(self) -> str:
        return f"{RUNNER_LOCK_KEY_PREFIX}:{self.lock_namespace}"

    def _entry_idem_key(self, bar_close_time_iso: str) -> str:
        return f"idem:entry:{self.lock_namespace}:{bar_close_time_iso}"

    def acquire_runner_lock(self, ttl_seconds: int) -> bool:
        token = str(uuid4())
        result = self.redis.set(self._runner_lock_key(), token, nx=True, ex=ttl_seconds)
        if result:
            self.runner_lock_token = token
            return True
        return False

    def release_runner_lock(self) -> None:
        if self.runner_lock_token is None:
            return
        runner_lock_key = self._runner_lock_key()
        current_token = self.redis.get(runner_lock_key)
        if isinstance(current_token, bytes):
            current_token_str = current_token.decode("utf-8")
        elif isinstance(current_token, str):
            current_token_str = current_token
        else:
            current_token_str = None
        if current_token_str == self.runner_lock_token:
            self.redis.delete(runner_lock_key)
        else:
            self.logger.warn("Runner lock token mismatch on release")
        self.runner_lock_token = None

    def mark_entry_attempt(self, bar_close_time_iso: str, ttl_seconds: int) -> bool:
        key = self._entry_idem_key(bar_close_time_iso)
        result = self.redis.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(result)

    def has_entry_attempt(self, bar_close_time_iso: str) -> bool:
        key = self._entry_idem_key(bar_close_time_iso)
        return self.redis.get(key) is not None

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        self.redis.set(f"tx:inflight:{self.lock_namespace}:{signature}", "1", ex=ttl_seconds)

    def clear_inflight_tx(self, signature: str) -> None:
        self.redis.delete(f"tx:inflight:{self.lock_namespace}:{signature}")
