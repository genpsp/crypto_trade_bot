from __future__ import annotations

from typing import Protocol

from pybot.domain.model.types import BotConfig, Pair, RunRecord, TradeRecord


class PersistencePort(Protocol):
    def get_current_config(self) -> BotConfig: ...

    def create_trade(self, trade: TradeRecord) -> None: ...

    def update_trade(self, trade_id: str, updates: dict) -> None: ...

    def find_open_trade(self, pair: Pair) -> TradeRecord | None: ...

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int: ...

    def save_run(self, run: RunRecord) -> None: ...

