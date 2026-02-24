from __future__ import annotations

from dataclasses import dataclass

import requests

from pybot.app.ports.execution_port import SubmitSwapRequest, SwapSide
from pybot.adapters.execution.http_retry import request_with_retry

QUOTE_API_URL = "https://lite-api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE_RETRY_ATTEMPTS = 4
QUOTE_RETRY_BASE_DELAY_SECONDS = 0.35


def get_mints(side: SwapSide) -> tuple[str, str]:
    if side == "BUY_SOL_WITH_USDC":
        return USDC_MINT, SOL_MINT
    return SOL_MINT, USDC_MINT


@dataclass
class JupiterQuote:
    raw: dict
    in_amount_atomic: int
    out_amount_atomic: int


def _format_fetch_error(error: Exception) -> str:
    return str(error)


class JupiterQuoteClient:
    def fetch_quote(self, request: SubmitSwapRequest) -> JupiterQuote:
        input_mint, output_mint = get_mints(request.side)
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(request.amount_atomic),
            "slippageBps": str(request.slippage_bps),
            "onlyDirectRoutes": str(request.only_direct_routes).lower(),
        }

        try:
            response = request_with_retry(
                lambda: requests.get(QUOTE_API_URL, params=params, timeout=30),
                attempts=QUOTE_RETRY_ATTEMPTS,
                base_delay_seconds=QUOTE_RETRY_BASE_DELAY_SECONDS,
                context="Jupiter quote failed",
            )
        except Exception as error:
            raise RuntimeError(f"Jupiter quote request failed: {_format_fetch_error(error)}") from error

        payload = response.json()
        in_amount = payload.get("inAmount")
        out_amount = payload.get("outAmount")
        if not isinstance(in_amount, str) or not isinstance(out_amount, str):
            raise RuntimeError("Jupiter quote payload is missing inAmount/outAmount")

        return JupiterQuote(
            raw=payload,
            in_amount_atomic=int(in_amount),
            out_amount_atomic=int(out_amount),
        )
