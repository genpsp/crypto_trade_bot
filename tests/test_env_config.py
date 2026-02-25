from __future__ import annotations

import unittest

from pybot.infra.config.env import load_env


class EnvConfigTest(unittest.TestCase):
    def test_load_env_without_wallet_key_path(self) -> None:
        env = load_env(
            {
                "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
                "REDIS_URL": "redis://localhost:6379",
                "GOOGLE_APPLICATION_CREDENTIALS": "secrets/firebase-service-account.json",
                "WALLET_KEY_PASSPHRASE": "test-passphrase",
            }
        )
        self.assertEqual(env.SOLANA_RPC_URL, "https://api.mainnet-beta.solana.com")
        self.assertEqual(env.REDIS_URL, "redis://localhost:6379")
        self.assertEqual(env.GOOGLE_APPLICATION_CREDENTIALS, "secrets/firebase-service-account.json")
        self.assertEqual(env.WALLET_KEY_PASSPHRASE, "test-passphrase")

    def test_load_env_requires_wallet_key_passphrase(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "WALLET_KEY_PASSPHRASE"):
            load_env(
                {
                    "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
                    "REDIS_URL": "redis://localhost:6379",
                    "GOOGLE_APPLICATION_CREDENTIALS": "secrets/firebase-service-account.json",
                }
            )


if __name__ == "__main__":
    unittest.main()
