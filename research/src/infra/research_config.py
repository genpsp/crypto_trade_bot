from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from apps.dex_bot.infra.config import schema as dex_schema_module
from apps.gmo_bot.infra.config import schema as gmo_schema_module


def _normalize_gmo_config_for_research(config: dict[str, Any]) -> dict[str, Any]:
    execution = config["execution"]
    return {
        "enabled": config["enabled"],
        "network": "gmo-coin",
        "broker": config["broker"],
        "pair": config["pair"],
        "direction": config["direction"],
        "signal_timeframe": config["signal_timeframe"],
        "strategy": dict(config["strategy"]),
        "risk": dict(config["risk"]),
        "execution": {
            "mode": execution["mode"],
            "broker": execution["broker"],
            "swap_provider": execution["broker"],
            "slippage_bps": execution["slippage_bps"],
            # The shared research backtest engine is quote-currency agnostic and only
            # needs a minimum quote notional for entry gating.
            "min_notional_usdc": float(execution["min_notional_jpy"]),
            "min_notional_jpy": float(execution["min_notional_jpy"]),
            "initial_quote_balance": 1_000_000.0,
            "leverage_multiplier": float(execution["leverage_multiplier"]),
            "margin_usage_ratio": float(execution["margin_usage_ratio"]),
        },
        "exit": dict(config["exit"]),
        "meta": dict(config["meta"]),
    }


def load_bot_config(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Config file not found: {source}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config payload must be object: {source}")

    broker = raw.get("broker")
    if broker == "GMO_COIN":
        latest_schema = importlib.reload(gmo_schema_module)
        return _normalize_gmo_config_for_research(latest_schema.parse_config(raw))

    latest_schema = importlib.reload(dex_schema_module)
    return latest_schema.parse_config(raw)
