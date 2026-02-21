from __future__ import annotations

from uuid import uuid4

from pybot.app.ports.execution_port import (
    ExecutionPort,
    SubmitSwapRequest,
    SwapConfirmation,
    SwapSubmission,
)
from pybot.app.ports.logger_port import LoggerPort
from pybot.adapters.execution.jupiter_quote_client import JupiterQuoteClient

USDC_ATOMIC_MULTIPLIER = 1_000_000
SOL_ATOMIC_MULTIPLIER = 1_000_000_000


class PaperExecutionAdapter(ExecutionPort):
    def __init__(self, quote_client: JupiterQuoteClient, logger: LoggerPort):
        self.quote_client = quote_client
        self.logger = logger

    def submit_swap(self, request: SubmitSwapRequest) -> SwapSubmission:
        quote = self.quote_client.fetch_quote(request)
        if request.side == "BUY_SOL_WITH_USDC":
            spent_quote_usdc = quote.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
            filled_base_sol = quote.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
        else:
            spent_quote_usdc = quote.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
            filled_base_sol = quote.in_amount_atomic / SOL_ATOMIC_MULTIPLIER

        avg_fill_price = spent_quote_usdc / filled_base_sol if filled_base_sol > 0 else 0
        paper_signature = f"PAPER_{uuid4()}"

        self.logger.info(
            "paper execution simulated",
            {
                "tx_signature": paper_signature,
                "side": request.side,
                "spent_quote_usdc": spent_quote_usdc,
                "filled_base_sol": filled_base_sol,
                "avg_fill_price": avg_fill_price,
            },
        )
        return SwapSubmission(
            tx_signature=paper_signature,
            in_amount_atomic=quote.in_amount_atomic,
            out_amount_atomic=quote.out_amount_atomic,
            order={"tx_signature": paper_signature},
            result={
                "status": "SIMULATED",
                "avg_fill_price": avg_fill_price,
                "spent_quote_usdc": spent_quote_usdc,
                "filled_base_sol": filled_base_sol,
            },
        )

    def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
        _ = tx_signature
        _ = timeout_ms
        return SwapConfirmation(confirmed=True)

    def get_mark_price(self, pair: str) -> float:
        if pair != "SOL/USDC":
            raise ValueError(f"Unsupported pair for mark price: {pair}")
        quote = self.quote_client.fetch_quote(
            SubmitSwapRequest(
                side="SELL_SOL_FOR_USDC",
                amount_atomic=SOL_ATOMIC_MULTIPLIER,
                slippage_bps=1,
                only_direct_routes=False,
            )
        )
        return quote.out_amount_atomic / USDC_ATOMIC_MULTIPLIER

    def get_available_quote_usdc(self, pair: str) -> float:
        if pair != "SOL/USDC":
            raise ValueError(f"Unsupported pair for quote balance: {pair}")
        # Paper mode uses virtual capital baseline for all-in sizing simulation.
        return 100.0
