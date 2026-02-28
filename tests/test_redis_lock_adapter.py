from __future__ import annotations

import unittest
from unittest.mock import Mock

from pybot.adapters.lock.redis_lock import RUNNER_LOCK_RELEASE_SCRIPT, RedisLockAdapter


class StubLogger:
    def __init__(self) -> None:
        self.warn_calls: list[tuple[str, dict | None]] = []

    def info(self, message: str, context: dict | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict | None = None) -> None:
        self.warn_calls.append((message, context))

    def error(self, message: str, context: dict | None = None) -> None:
        _ = message
        _ = context


class RedisLockAdapterTest(unittest.TestCase):
    def test_release_runner_lock_uses_atomic_eval(self) -> None:
        redis = Mock()
        redis.set.return_value = True
        redis.eval.return_value = 1
        logger = StubLogger()
        lock = RedisLockAdapter(redis, logger, lock_namespace="ema_pullback_2h_long_v0")

        acquired = lock.acquire_runner_lock(120)
        self.assertTrue(acquired)
        token = lock.runner_lock_token
        self.assertIsNotNone(token)

        lock.release_runner_lock()

        redis.eval.assert_called_once_with(
            RUNNER_LOCK_RELEASE_SCRIPT,
            1,
            "lock:runner:ema_pullback_2h_long_v0",
            token,
        )
        self.assertIsNone(lock.runner_lock_token)

    def test_has_inflight_tx_checks_namespaced_key(self) -> None:
        redis = Mock()
        redis.get.return_value = "1"
        logger = StubLogger()
        lock = RedisLockAdapter(redis, logger, lock_namespace="ema_pullback_15m_both_v0")

        self.assertTrue(lock.has_inflight_tx("sig-123"))
        redis.get.assert_called_once_with("tx:inflight:ema_pullback_15m_both_v0:sig-123")

    def test_clear_entry_attempt_deletes_namespaced_key(self) -> None:
        redis = Mock()
        logger = StubLogger()
        lock = RedisLockAdapter(redis, logger, lock_namespace="ema_pullback_15m_both_v0")

        lock.clear_entry_attempt("2026-02-28T03:00:00Z")

        redis.delete.assert_called_once_with("idem:entry:ema_pullback_15m_both_v0:2026-02-28T03:00:00Z")


if __name__ == "__main__":
    unittest.main()
