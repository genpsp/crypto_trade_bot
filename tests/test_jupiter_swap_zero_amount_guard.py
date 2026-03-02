from __future__ import annotations

import unittest
from typing import Any

from pybot.adapters.execution.jupiter_quote_client import JupiterQuote
from pybot.adapters.execution.jupiter_swap import JupiterSwapAdapter
from pybot.app.ports.execution_port import SubmitSwapRequest


class InMemoryLogger:
    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context


class _FakeQuoteClient:
    def __init__(self, quote: JupiterQuote):
        self.quote = quote

    def fetch_quote(self, request: SubmitSwapRequest) -> JupiterQuote:
        _ = request
        return self.quote


class _FakeSolanaSender:
    def __init__(self) -> None:
        self.sent = 0

    def send_versioned_transaction_base64(self, serialized_base64: str) -> str:
        _ = serialized_base64
        self.sent += 1
        return "sig-1"

    def confirm_signature(self, signature: str, timeout_ms: int) -> Any:
        _ = signature
        _ = timeout_ms
        raise AssertionError("unused")

    def get_public_key_base58(self) -> str:
        return "dummy"

    def get_spl_token_balance_ui_amount(self, mint: str) -> float:
        _ = mint
        return 0.0

    def get_native_sol_balance_ui_amount(self) -> float:
        return 0.0


class JupiterSwapZeroAmountGuardTest(unittest.TestCase):
    def test_submit_swap_rejects_quote_with_zero_amount_leg(self) -> None:
        quote = JupiterQuote(
            raw={
                "inAmount": "39000000",
                "outAmount": "450000000",
                "routePlan": [
                    {
                        "swapInfo": {
                            "inAmount": "39000000",
                            "outAmount": "0",
                        }
                    }
                ],
            },
            in_amount_atomic=39_000_000,
            out_amount_atomic=450_000_000,
        )
        sender = _FakeSolanaSender()
        adapter = JupiterSwapAdapter(
            quote_client=_FakeQuoteClient(quote),
            solana_sender=sender,  # type: ignore[arg-type]
            logger=InMemoryLogger(),
        )

        with self.assertRaisesRegex(RuntimeError, "zero-amount leg"):
            adapter.submit_swap(
                SubmitSwapRequest(
                    side="BUY_SOL_WITH_USDC",
                    amount_atomic=39_000_000,
                    slippage_bps=3,
                    only_direct_routes=False,
                )
            )
        self.assertEqual(0, sender.sent)

    def test_submit_swap_rejects_quote_with_zero_out_amount(self) -> None:
        quote = JupiterQuote(
            raw={"inAmount": "39000000", "outAmount": "0"},
            in_amount_atomic=39_000_000,
            out_amount_atomic=0,
        )
        sender = _FakeSolanaSender()
        adapter = JupiterSwapAdapter(
            quote_client=_FakeQuoteClient(quote),
            solana_sender=sender,  # type: ignore[arg-type]
            logger=InMemoryLogger(),
        )

        with self.assertRaisesRegex(RuntimeError, "quote amount is zero"):
            adapter.submit_swap(
                SubmitSwapRequest(
                    side="BUY_SOL_WITH_USDC",
                    amount_atomic=39_000_000,
                    slippage_bps=3,
                    only_direct_routes=False,
                )
            )
        self.assertEqual(0, sender.sent)


if __name__ == "__main__":
    unittest.main()
