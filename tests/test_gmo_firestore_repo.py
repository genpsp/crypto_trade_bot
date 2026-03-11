from __future__ import annotations

import unittest

from apps.gmo_bot.adapters.persistence.firestore_repo import _extract_trade_date_from_trade_id


class GmoFirestoreRepositoryDateParseTest(unittest.TestCase):
    def test_extract_trade_date_from_trade_id_uses_jst_bucket(self) -> None:
        self.assertEqual(
            "2026-02-26",
            _extract_trade_date_from_trade_id("2026-02-25T21:45:00Z_gmo_ema_pullback_15m_both_v0_SHORT"),
        )


if __name__ == "__main__":
    unittest.main()
