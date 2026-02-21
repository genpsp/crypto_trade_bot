from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ExitReason = Literal["TAKE_PROFIT", "STOP_LOSS", "STOP_LOSS_AND_TP_SAME_BAR", "OPEN"]


@dataclass
class BacktestTrade:
    entry_time: str
    exit_time: str | None
    entry_price: float
    stop_price: float
    take_profit_price: float
    exit_price: float | None
    exit_reason: ExitReason
    pnl_pct: float | None
    r_multiple: float | None
    holding_bars: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestSummary:
    total_bars: int
    decision_enter_count: int
    decision_no_signal_count: int
    closed_trades: int
    open_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    average_pnl_pct: float
    total_pnl_pct: float
    average_r_multiple: float
    first_bar_close_time: str
    last_bar_close_time: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestReport:
    summary: BacktestSummary
    no_signal_reason_counts: dict[str, int]
    trades: list[BacktestTrade]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "no_signal_reason_counts": self.no_signal_reason_counts,
            "trades": [trade.to_dict() for trade in self.trades],
        }
