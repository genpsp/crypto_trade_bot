from __future__ import annotations

from uuid import uuid4

from apps.gmo_bot.app.ports.execution_port import (
    ExecutionPort,
    OrderConfirmation,
    OrderSubmission,
    ProtectiveExitOrdersSubmission,
    SubmitCloseOrderRequest,
    SubmitEntryOrderRequest,
    SubmitProtectiveExitOrdersRequest,
    SymbolRule,
)
from apps.gmo_bot.app.ports.logger_port import LoggerPort

PAPER_INITIAL_MARGIN_JPY = 1_000_000.0
DEFAULT_SYMBOL_RULE = SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01)


class PaperExecutionAdapter(ExecutionPort):
    def __init__(self, logger: LoggerPort):
        self.logger = logger
        self._latest_price = 0.0

    def submit_entry_order(self, request: SubmitEntryOrderRequest) -> OrderSubmission:
        fill_price = request.reference_price
        self._latest_price = fill_price
        order_id = self._next_order_id()
        result = {
            "status": "SIMULATED",
            "avg_fill_price": fill_price,
            "filled_base_sol": request.size_sol,
            "filled_quote_jpy": fill_price * request.size_sol,
            "fee_jpy": 0.0,
            "execution_ids": [f"PAPER_EXEC_{order_id}"],
            "lots": [{"position_id": order_id, "size_sol": request.size_sol}],
        }
        self.logger.info(
            "paper gmo entry simulated",
            {"order_id": order_id, "side": request.side, "size_sol": request.size_sol, "fill_price": fill_price},
        )
        return OrderSubmission(order_id=order_id, order={"order_id": order_id}, result=result)

    def submit_close_order(self, request: SubmitCloseOrderRequest) -> OrderSubmission:
        size_sol = sum(lot["size_sol"] for lot in request.lots)
        fill_price = request.reference_price
        self._latest_price = fill_price
        order_id = self._next_order_id()
        result = {
            "status": "SIMULATED",
            "avg_fill_price": fill_price,
            "filled_base_sol": size_sol,
            "filled_quote_jpy": fill_price * size_sol,
            "fee_jpy": 0.0,
            "execution_ids": [f"PAPER_EXEC_{order_id}"],
            "lots": [],
        }
        self.logger.info(
            "paper gmo close simulated",
            {"order_id": order_id, "side": request.side, "size_sol": size_sol, "fill_price": fill_price},
        )
        return OrderSubmission(order_id=order_id, order={"order_id": order_id}, result=result)

    def submit_protective_exit_orders(self, request: SubmitProtectiveExitOrdersRequest) -> ProtectiveExitOrdersSubmission:
        sl_order_id = self._next_order_id()
        self.logger.info(
            "paper gmo protective stop simulated",
            {
                "sl_order_id": sl_order_id,
                "side": request.side,
                "stop_price": request.stop_price,
            },
        )
        return ProtectiveExitOrdersSubmission(
            stop_loss_order=OrderSubmission(order_id=sl_order_id, order={"order_id": sl_order_id, "price": request.stop_price}),
        )

    def confirm_order(self, order_id: int, timeout_ms: int) -> OrderConfirmation:
        _ = order_id
        _ = timeout_ms
        return OrderConfirmation(confirmed=True)

    def cancel_order(self, order_id: int) -> None:
        self.logger.info("paper gmo cancel simulated", {"order_id": order_id})

    def get_order(self, order_id: int):
        _ = order_id
        return None

    def get_executions(self, order_id: int):
        _ = order_id
        return []

    def get_mark_price(self, pair: str) -> float:
        if pair != "SOL/JPY":
            raise ValueError(f"Unsupported pair for paper price: {pair}")
        return self._latest_price if self._latest_price > 0 else 20_000.0

    def get_available_margin_jpy(self) -> float:
        return PAPER_INITIAL_MARGIN_JPY

    def get_symbol_rule(self, pair: str) -> SymbolRule:
        if pair != "SOL/JPY":
            raise ValueError(f"Unsupported pair for paper rule: {pair}")
        return DEFAULT_SYMBOL_RULE

    def _next_order_id(self) -> int:
        return int(uuid4().int % 10_000_000_000)
