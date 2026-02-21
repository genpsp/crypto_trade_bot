from __future__ import annotations

from typing import Any

from pybot.domain.model.types import BotConfig

ALLOWED_TOP_LEVEL_KEYS = {
    "enabled",
    "network",
    "pair",
    "direction",
    "signal_timeframe",
    "strategy",
    "risk",
    "execution",
    "exit",
    "meta",
}

DEFAULT_VOLATILE_ATR_PCT_THRESHOLD = 1.30
DEFAULT_STORM_ATR_PCT_THRESHOLD = 1.40
DEFAULT_VOLATILE_SIZE_MULTIPLIER = 0.75
DEFAULT_STORM_SIZE_MULTIPLIER = 0.50


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_config(data: Any) -> BotConfig:
    _require(isinstance(data, dict), "config/current must be an object")
    unknown_keys = set(data.keys()) - ALLOWED_TOP_LEVEL_KEYS
    _require(not unknown_keys, f"config/current has unknown keys: {sorted(unknown_keys)}")

    _require(isinstance(data.get("enabled"), bool), "enabled must be boolean")
    _require(data.get("network") == "mainnet-beta", "network must be 'mainnet-beta'")
    _require(data.get("pair") == "SOL/USDC", "pair must be 'SOL/USDC'")
    _require(data.get("direction") == "LONG_ONLY", "direction must be 'LONG_ONLY'")
    _require(data.get("signal_timeframe") in ("2h", "4h"), "signal_timeframe must be '2h' or '4h'")

    strategy = data.get("strategy")
    _require(isinstance(strategy, dict), "strategy must be object")
    _require(strategy.get("name") == "ema_trend_pullback_v0", "strategy.name must be ema_trend_pullback_v0")
    _require(isinstance(strategy.get("ema_fast_period"), int) and strategy["ema_fast_period"] > 0, "strategy.ema_fast_period must be positive int")
    _require(isinstance(strategy.get("ema_slow_period"), int) and strategy["ema_slow_period"] > 0, "strategy.ema_slow_period must be positive int")
    _require(
        isinstance(strategy.get("swing_low_lookback_bars"), int) and strategy["swing_low_lookback_bars"] > 0,
        "strategy.swing_low_lookback_bars must be positive int",
    )
    _require(strategy.get("entry") == "ON_BAR_CLOSE", "strategy.entry must be ON_BAR_CLOSE")

    risk = data.get("risk")
    _require(isinstance(risk, dict), "risk must be object")
    _require(
        isinstance(risk.get("max_loss_per_trade_pct"), (int, float)) and risk["max_loss_per_trade_pct"] > 0,
        "risk.max_loss_per_trade_pct must be positive",
    )
    _require(
        isinstance(risk.get("max_trades_per_day"), int) and risk["max_trades_per_day"] > 0,
        "risk.max_trades_per_day must be positive int",
    )
    volatile_atr_pct_threshold = risk.get(
        "volatile_atr_pct_threshold", DEFAULT_VOLATILE_ATR_PCT_THRESHOLD
    )
    storm_atr_pct_threshold = risk.get("storm_atr_pct_threshold", DEFAULT_STORM_ATR_PCT_THRESHOLD)
    volatile_size_multiplier = risk.get("volatile_size_multiplier", DEFAULT_VOLATILE_SIZE_MULTIPLIER)
    storm_size_multiplier = risk.get("storm_size_multiplier", DEFAULT_STORM_SIZE_MULTIPLIER)
    _require(
        isinstance(volatile_atr_pct_threshold, (int, float)) and volatile_atr_pct_threshold > 0,
        "risk.volatile_atr_pct_threshold must be positive",
    )
    _require(
        isinstance(storm_atr_pct_threshold, (int, float)) and storm_atr_pct_threshold > 0,
        "risk.storm_atr_pct_threshold must be positive",
    )
    _require(
        storm_atr_pct_threshold >= volatile_atr_pct_threshold,
        "risk.storm_atr_pct_threshold must be >= risk.volatile_atr_pct_threshold",
    )
    _require(
        isinstance(volatile_size_multiplier, (int, float)) and 0 < volatile_size_multiplier <= 1,
        "risk.volatile_size_multiplier must be > 0 and <= 1",
    )
    _require(
        isinstance(storm_size_multiplier, (int, float)) and 0 < storm_size_multiplier <= 1,
        "risk.storm_size_multiplier must be > 0 and <= 1",
    )
    _require(
        storm_size_multiplier <= volatile_size_multiplier,
        "risk.storm_size_multiplier must be <= risk.volatile_size_multiplier",
    )

    execution = data.get("execution")
    _require(isinstance(execution, dict), "execution must be object")
    mode = execution.get("mode", "PAPER")
    _require(mode in ("PAPER", "LIVE"), "execution.mode must be PAPER or LIVE")
    _require(execution.get("swap_provider") == "JUPITER", "execution.swap_provider must be JUPITER")
    _require(
        isinstance(execution.get("slippage_bps"), int) and execution["slippage_bps"] > 0,
        "execution.slippage_bps must be positive int",
    )
    _require(
        isinstance(execution.get("min_notional_usdc"), (int, float))
        and execution["min_notional_usdc"] > 0,
        "execution.min_notional_usdc must be positive",
    )
    _require(
        isinstance(execution.get("only_direct_routes"), bool),
        "execution.only_direct_routes must be boolean",
    )

    exit_config = data.get("exit")
    _require(isinstance(exit_config, dict), "exit must be object")
    _require(exit_config.get("stop") == "SWING_LOW", "exit.stop must be SWING_LOW")
    _require(
        isinstance(exit_config.get("take_profit_r_multiple"), (int, float))
        and exit_config["take_profit_r_multiple"] > 0,
        "exit.take_profit_r_multiple must be positive",
    )

    meta = data.get("meta")
    _require(isinstance(meta, dict), "meta must be object")
    _require(
        isinstance(meta.get("config_version"), int) and meta["config_version"] > 0,
        "meta.config_version must be positive int",
    )
    _require(isinstance(meta.get("note"), str) and len(meta["note"]) > 0, "meta.note must be non-empty")

    parsed: BotConfig = {
        "enabled": data["enabled"],
        "network": data["network"],
        "pair": data["pair"],
        "direction": data["direction"],
        "signal_timeframe": data["signal_timeframe"],
        "strategy": {
            "name": strategy["name"],
            "ema_fast_period": strategy["ema_fast_period"],
            "ema_slow_period": strategy["ema_slow_period"],
            "swing_low_lookback_bars": strategy["swing_low_lookback_bars"],
            "entry": strategy["entry"],
        },
        "risk": {
            "max_loss_per_trade_pct": float(risk["max_loss_per_trade_pct"]),
            "max_trades_per_day": risk["max_trades_per_day"],
            "volatile_atr_pct_threshold": float(volatile_atr_pct_threshold),
            "storm_atr_pct_threshold": float(storm_atr_pct_threshold),
            "volatile_size_multiplier": float(volatile_size_multiplier),
            "storm_size_multiplier": float(storm_size_multiplier),
        },
        "execution": {
            "mode": mode,
            "swap_provider": execution["swap_provider"],
            "slippage_bps": execution["slippage_bps"],
            "min_notional_usdc": float(execution["min_notional_usdc"]),
            "only_direct_routes": execution["only_direct_routes"],
        },
        "exit": {
            "stop": exit_config["stop"],
            "take_profit_r_multiple": float(exit_config["take_profit_r_multiple"]),
        },
        "meta": {
            "config_version": meta["config_version"],
            "note": meta["note"],
        },
    }
    return parsed
