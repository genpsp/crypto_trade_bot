from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from apps.dex_bot.adapters.market_data.ohlcv_provider import OhlcvProvider as DexOhlcvProvider
from apps.dex_bot.domain.model.types import OhlcvBar
from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.adapters.market_data.ohlcv_provider import OhlcvProvider as GmoOhlcvProvider

Broker = Literal["DEX", "GMO_COIN"]


@runtime_checkable
class OhlcvProviderProtocol(Protocol):
    def fetch_bars(self, pair: str, timeframe: str, limit: int) -> list[OhlcvBar]: ...


class BackfillOhlcvProviderProtocol(OhlcvProviderProtocol, Protocol):
    def fetch_bars_backfill(self, pair: str, timeframe: str, total_limit: int) -> list[OhlcvBar]: ...


def infer_broker(pair: str) -> Broker:
    if pair == "SOL/USDC":
        return "DEX"
    if pair == "SOL/JPY":
        return "GMO_COIN"
    raise ValueError(f"unsupported pair: {pair}")


def get_provider(broker: Broker | str | None = None, pair: str | None = None) -> OhlcvProviderProtocol:
    resolved_broker = infer_broker(pair) if broker is None and pair is not None else broker
    if resolved_broker == "DEX":
        if pair is not None and pair != "SOL/USDC":
            raise ValueError(f"DEX provider does not support pair: {pair}")
        return DexOhlcvProvider()
    if resolved_broker == "GMO_COIN":
        if pair is not None and pair != "SOL/JPY":
            raise ValueError(f"GMO_COIN provider does not support pair: {pair}")
        return GmoOhlcvProvider(client=GmoApiClient(api_key="", api_secret=""))
    raise ValueError(f"unsupported broker: {broker}")


def fetch_recent_bars(
    provider: OhlcvProviderProtocol,
    *,
    pair: str,
    timeframe: str,
    limit: int,
) -> list[OhlcvBar]:
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    if hasattr(provider, "fetch_bars_backfill") and limit > 1000:
        backfill_provider = provider  # type: ignore[assignment]
        return backfill_provider.fetch_bars_backfill(  # type: ignore[attr-defined]
            pair=pair,
            timeframe=timeframe,
            total_limit=limit,
        )
    return provider.fetch_bars(pair=pair, timeframe=timeframe, limit=limit)
