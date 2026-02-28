from __future__ import annotations

from uuid import uuid4

from redis import Redis

from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort

RUNNER_LOCK_KEY_PREFIX = "lock:runner"
INFLIGHT_TX_KEY_PREFIX = "tx:inflight"
RUNNER_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


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

    def _inflight_tx_key(self, signature: str) -> str:
        return f"{INFLIGHT_TX_KEY_PREFIX}:{self.lock_namespace}:{signature}"

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
        runner_lock_token = self.runner_lock_token
        try:
            released = self.redis.eval(
                RUNNER_LOCK_RELEASE_SCRIPT,
                1,
                runner_lock_key,
                runner_lock_token,
            )
            if released != 1:
                self.logger.warn("Runner lock token mismatch on release")
        except Exception as error:
            self.logger.warn(
                "Runner lock release failed",
                {"error": str(error), "lock_key": runner_lock_key},
            )
        self.runner_lock_token = None

    def mark_entry_attempt(self, bar_close_time_iso: str, ttl_seconds: int) -> bool:
        key = self._entry_idem_key(bar_close_time_iso)
        result = self.redis.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(result)

    def has_entry_attempt(self, bar_close_time_iso: str) -> bool:
        key = self._entry_idem_key(bar_close_time_iso)
        return self.redis.get(key) is not None

    def clear_entry_attempt(self, bar_close_time_iso: str) -> None:
        key = self._entry_idem_key(bar_close_time_iso)
        self.redis.delete(key)

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        self.redis.set(self._inflight_tx_key(signature), "1", ex=ttl_seconds)

    def has_inflight_tx(self, signature: str) -> bool:
        return self.redis.get(self._inflight_tx_key(signature)) is not None

    def clear_inflight_tx(self, signature: str) -> None:
        self.redis.delete(self._inflight_tx_key(signature))
