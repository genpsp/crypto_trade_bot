from __future__ import annotations

from typing import Any

from apps.dex_bot.infra.config.schema import _parse_exit, _parse_risk, _parse_strategy, _require
from apps.gmo_bot.domain.model.types import BotConfig

ALLOWED_TOP_LEVEL_KEYS = {
    "enabled",
    "broker",
    "pair",
    "direction",
    "signal_timeframe",
    "strategy",
    "risk",
    "execution",
    "exit",
    "meta",
}


def parse_config(data: Any) -> BotConfig:
    _require(isinstance(data, dict), "config/current must be an object")
    unknown_keys = set(data.keys()) - ALLOWED_TOP_LEVEL_KEYS
    _require(not unknown_keys, f"config/current has unknown keys: {sorted(unknown_keys)}")

    _require(isinstance(data.get("enabled"), bool), "enabled must be boolean")
    _require(data.get("broker") == "GMO_COIN", "broker must be 'GMO_COIN'")
    _require(data.get("pair") == "SOL/JPY", "pair must be 'SOL/JPY'")
    _require(data.get("direction") in ("LONG", "SHORT", "BOTH"), "direction must be LONG, SHORT or BOTH")
    _require(
        data.get("signal_timeframe") in ("15m", "2h", "4h"),
        "signal_timeframe must be '15m', '2h' or '4h'",
    )

    strategy = _parse_strategy(data.get("strategy"), "strategy")
    if strategy["name"] == "ema_trend_pullback_15m_v0":
        _require(
            data["signal_timeframe"] == "15m",
            "ema_trend_pullback_15m_v0 requires signal_timeframe='15m'",
        )
    if strategy["name"] == "ema_trend_pullback_v0":
        _require(
            data["signal_timeframe"] in ("2h", "4h"),
            "ema_trend_pullback_v0 requires signal_timeframe='2h' or '4h'",
        )
        _require(
            data["direction"] == "LONG",
            "ema_trend_pullback_v0 requires direction='LONG'",
        )
    if strategy["name"] == "storm_short_v0":
        _require(
            data["direction"] == "SHORT",
            "storm_short_v0 requires direction='SHORT'",
        )

    risk = _parse_risk(data.get("risk"), "risk")

    execution = data.get("execution")
    _require(isinstance(execution, dict), "execution must be object")
    mode = execution.get("mode", "PAPER")
    _require(mode in ("PAPER", "LIVE"), "execution.mode must be PAPER or LIVE")
    _require(execution.get("broker") == "GMO_COIN", "execution.broker must be GMO_COIN")
    _require(
        isinstance(execution.get("slippage_bps"), int) and execution["slippage_bps"] > 0,
        "execution.slippage_bps must be positive int",
    )
    _require(
        isinstance(execution.get("min_notional_jpy"), (int, float)) and execution["min_notional_jpy"] > 0,
        "execution.min_notional_jpy must be positive",
    )
    leverage_multiplier = execution.get("leverage_multiplier", 1.0)
    margin_usage_ratio = execution.get("margin_usage_ratio", 0.99)
    _require(
        isinstance(leverage_multiplier, (int, float)) and 0 < float(leverage_multiplier) <= 2.0,
        "execution.leverage_multiplier must be > 0 and <= 2.0",
    )
    _require(
        isinstance(margin_usage_ratio, (int, float)) and 0 < float(margin_usage_ratio) <= 1.0,
        "execution.margin_usage_ratio must be > 0 and <= 1.0",
    )

    exit_config = _parse_exit(data.get("exit"), "exit")

    meta = data.get("meta")
    _require(isinstance(meta, dict), "meta must be object")
    _require(
        isinstance(meta.get("config_version"), int) and meta["config_version"] > 0,
        "meta.config_version must be positive int",
    )
    _require(isinstance(meta.get("note"), str) and len(meta["note"]) > 0, "meta.note must be non-empty")

    return {
        "enabled": data["enabled"],
        "broker": data["broker"],
        "pair": data["pair"],
        "direction": data["direction"],
        "signal_timeframe": data["signal_timeframe"],
        "strategy": strategy,
        "risk": risk,  # type: ignore[typeddict-item]
        "execution": {
            "mode": mode,
            "broker": execution["broker"],
            "slippage_bps": execution["slippage_bps"],
            "min_notional_jpy": float(execution["min_notional_jpy"]),
            "leverage_multiplier": float(leverage_multiplier),
            "margin_usage_ratio": float(margin_usage_ratio),
        },
        "exit": exit_config,  # type: ignore[typeddict-item]
        "meta": {
            "config_version": meta["config_version"],
            "note": meta["note"],
        },
    }
