from __future__ import annotations

from dataclasses import dataclass
import json

import requests
from redis import Redis

from pybot.app.ports.execution_port import SubmitSwapRequest, SwapSide
from pybot.adapters.execution.http_retry import request_with_retry

QUOTE_API_URL = "https://lite-api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE_RETRY_ATTEMPTS = 4
QUOTE_RETRY_BASE_DELAY_SECONDS = 0.35
MARK_PRICE_QUOTE_CACHE_TTL_SECONDS = 2
QUOTE_HTTP_TIMEOUT_SECONDS = 8


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
    def __init__(self, redis: Redis | None = None):
        self.redis = redis

    def _build_quote_cache_key(self, request: SubmitSwapRequest) -> str:
        return (
            f"cache:jupiter:quote:{request.side}:{request.amount_atomic}:"
            f"{request.slippage_bps}:{int(request.only_direct_routes)}"
        )

    def _get_cached_quote(self, cache_key: str) -> JupiterQuote | None:
        if self.redis is None:
            return None
        try:
            cached_payload = self.redis.get(cache_key)
        except Exception:
            return None
        if cached_payload is None:
            return None
        if isinstance(cached_payload, bytes):
            raw_payload = cached_payload.decode("utf-8")
        elif isinstance(cached_payload, str):
            raw_payload = cached_payload
        else:
            return None
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        in_amount = parsed.get("inAmount")
        out_amount = parsed.get("outAmount")
        if not isinstance(in_amount, str) or not isinstance(out_amount, str):
            return None
        return JupiterQuote(raw=parsed, in_amount_atomic=int(in_amount), out_amount_atomic=int(out_amount))

    def _set_cached_quote(self, cache_key: str, payload: dict, ttl_seconds: int) -> None:
        if self.redis is None or ttl_seconds <= 0:
            return
        try:
            self.redis.set(cache_key, json.dumps(payload), ex=ttl_seconds)
        except Exception:
            return

    def fetch_quote(self, request: SubmitSwapRequest, *, cache_ttl_seconds: int = 0) -> JupiterQuote:
        cache_key: str | None = None
        if cache_ttl_seconds > 0:
            cache_key = self._build_quote_cache_key(request)
            cached_quote = self._get_cached_quote(cache_key)
            if cached_quote is not None:
                return cached_quote

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
                lambda: requests.get(QUOTE_API_URL, params=params, timeout=QUOTE_HTTP_TIMEOUT_SECONDS),
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

        if cache_key is not None:
            self._set_cached_quote(cache_key, payload, cache_ttl_seconds)

        return JupiterQuote(
            raw=payload,
            in_amount_atomic=int(in_amount),
            out_amount_atomic=int(out_amount),
        )
