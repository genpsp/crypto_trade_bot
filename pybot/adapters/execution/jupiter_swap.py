from __future__ import annotations

from typing import Any

import requests

from pybot.app.ports.execution_port import (
    ExecutionPort,
    SubmitSwapRequest,
    SwapConfirmation,
    SwapSubmission,
)
from pybot.app.ports.logger_port import LoggerPort
from pybot.adapters.execution.http_retry import request_with_retry
from pybot.adapters.execution.jupiter_quote_client import JupiterQuoteClient
from pybot.adapters.execution.jupiter_quote_client import USDC_MINT
from pybot.adapters.execution.solana_sender import SolanaSender

SWAP_API_URL = "https://lite-api.jup.ag/swap/v1/swap"
SOL_ATOMIC_MULTIPLIER = 1_000_000_000
USDC_ATOMIC_MULTIPLIER = 1_000_000
SWAP_RETRY_ATTEMPTS = 4
SWAP_RETRY_BASE_DELAY_SECONDS = 0.35
SWAP_HTTP_TIMEOUT_SECONDS = 8


def _parse_atomic_amount(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _has_zero_amount_route_leg(quote_response: dict[str, Any]) -> bool:
    route_plan = quote_response.get("routePlan")
    if not isinstance(route_plan, list):
        return False

    for leg in route_plan:
        if not isinstance(leg, dict):
            continue
        swap_info = leg.get("swapInfo")
        if not isinstance(swap_info, dict):
            continue
        in_amount = _parse_atomic_amount(swap_info.get("inAmount"))
        out_amount = _parse_atomic_amount(swap_info.get("outAmount"))
        if in_amount is not None and in_amount <= 0:
            return True
        if out_amount is not None and out_amount <= 0:
            return True
    return False


class JupiterSwapAdapter(ExecutionPort):
    def __init__(self, quote_client: JupiterQuoteClient, solana_sender: SolanaSender, logger: LoggerPort):
        self.quote_client = quote_client
        self.solana_sender = solana_sender
        self.logger = logger

    @staticmethod
    def _assert_pair_supported(pair: str, context: str) -> None:
        if pair != "SOL/USDC":
            raise ValueError(f"Unsupported pair for {context}: {pair}")

    def submit_swap(self, request: SubmitSwapRequest) -> SwapSubmission:
        quote = self.quote_client.fetch_quote(request)
        if quote.in_amount_atomic <= 0 or quote.out_amount_atomic <= 0:
            raise RuntimeError("Jupiter quote amount is zero")
        if _has_zero_amount_route_leg(quote.raw):
            raise RuntimeError("Jupiter quote route contains zero-amount leg")

        swap_transaction = self._fetch_swap_transaction(quote.raw)
        tx_signature = self.solana_sender.send_versioned_transaction_base64(swap_transaction)

        if request.side == "BUY_SOL_WITH_USDC":
            spent_quote_usdc = quote.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
            filled_base_sol = quote.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
        else:
            spent_quote_usdc = quote.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
            filled_base_sol = quote.in_amount_atomic / SOL_ATOMIC_MULTIPLIER

        avg_fill_price = spent_quote_usdc / filled_base_sol if filled_base_sol > 0 else 0
        return SwapSubmission(
            tx_signature=tx_signature,
            in_amount_atomic=quote.in_amount_atomic,
            out_amount_atomic=quote.out_amount_atomic,
            order={"tx_signature": tx_signature},
            result={
                "status": "ESTIMATED",
                "avg_fill_price": avg_fill_price,
                "spent_quote_usdc": spent_quote_usdc,
                "filled_base_sol": filled_base_sol,
            },
        )

    def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
        confirmation = self.solana_sender.confirm_signature(tx_signature, timeout_ms)
        return SwapConfirmation(confirmed=confirmation.confirmed, error=confirmation.error)

    def get_transaction_fee_lamports(self, tx_signature: str) -> int | None:
        fee_getter = getattr(self.solana_sender, "get_transaction_fee_lamports", None)
        if not callable(fee_getter):
            return None
        fee = fee_getter(tx_signature)
        if isinstance(fee, int) and fee >= 0:
            return fee
        return None

    def get_mark_price(self, pair: str) -> float:
        self._assert_pair_supported(pair, "mark price")

        quote = self.quote_client.fetch_quote(
            SubmitSwapRequest(
                side="SELL_SOL_FOR_USDC",
                amount_atomic=SOL_ATOMIC_MULTIPLIER,
                slippage_bps=1,
                only_direct_routes=False,
            )
        )
        out_usdc = quote.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
        if out_usdc <= 0:
            raise RuntimeError(
                f"Invalid mark price quote: outAmountAtomic={quote.out_amount_atomic}"
            )
        return out_usdc

    def get_available_quote_usdc(self, pair: str) -> float:
        self._assert_pair_supported(pair, "quote balance")
        return self.solana_sender.get_spl_token_balance_ui_amount(USDC_MINT)

    def get_available_base_sol(self, pair: str) -> float:
        self._assert_pair_supported(pair, "base balance")
        return self.solana_sender.get_native_sol_balance_ui_amount()

    def _fetch_swap_transaction(self, quote_response: dict[str, Any]) -> str:
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": self.solana_sender.get_public_key_base58(),
            "wrapAndUnwrapSol": True,
        }
        response = request_with_retry(
            lambda: requests.post(
                SWAP_API_URL,
                json=payload,
                timeout=SWAP_HTTP_TIMEOUT_SECONDS,
                headers={"Content-Type": "application/json"},
            ),
            attempts=SWAP_RETRY_ATTEMPTS,
            base_delay_seconds=SWAP_RETRY_BASE_DELAY_SECONDS,
            context="Jupiter swap failed",
        )

        data = response.json()
        swap_transaction = data.get("swapTransaction")
        if not isinstance(swap_transaction, str):
            raise RuntimeError("Jupiter swap payload is missing swapTransaction")
        self.logger.info("Swap transaction generated by Jupiter")
        return swap_transaction
