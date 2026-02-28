from __future__ import annotations

from typing import Any

from pybot.domain.model.types import BotConfig, StrategyConfig

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


def _parse_strategy(strategy: Any, prefix: str) -> StrategyConfig:
    _require(isinstance(strategy, dict), f"{prefix} must be object")
    _require(
        strategy.get("name")
        in ("ema_trend_pullback_v0", "ema_trend_pullback_15m_v0", "storm_short_v0"),
        f"{prefix}.name must be ema_trend_pullback_v0, ema_trend_pullback_15m_v0 or storm_short_v0",
    )
    _require(
        isinstance(strategy.get("ema_fast_period"), int) and strategy["ema_fast_period"] > 0,
        f"{prefix}.ema_fast_period must be positive int",
    )
    _require(
        isinstance(strategy.get("ema_slow_period"), int) and strategy["ema_slow_period"] > 0,
        f"{prefix}.ema_slow_period must be positive int",
    )
    _require(
        isinstance(strategy.get("swing_low_lookback_bars"), int)
        and strategy["swing_low_lookback_bars"] > 0,
        f"{prefix}.swing_low_lookback_bars must be positive int",
    )
    _require(strategy.get("entry") == "ON_BAR_CLOSE", f"{prefix}.entry must be ON_BAR_CLOSE")
    return {
        "name": strategy["name"],
        "ema_fast_period": strategy["ema_fast_period"],
        "ema_slow_period": strategy["ema_slow_period"],
        "swing_low_lookback_bars": strategy["swing_low_lookback_bars"],
        "entry": strategy["entry"],
    }


def _parse_risk(risk: Any, prefix: str) -> dict[str, float | int]:
    _require(isinstance(risk, dict), f"{prefix} must be object")
    _require(
        isinstance(risk.get("max_loss_per_trade_pct"), (int, float))
        and risk["max_loss_per_trade_pct"] > 0,
        f"{prefix}.max_loss_per_trade_pct must be positive",
    )
    _require(
        isinstance(risk.get("max_trades_per_day"), int) and risk["max_trades_per_day"] > 0,
        f"{prefix}.max_trades_per_day must be positive int",
    )
    volatile_atr_pct_threshold = risk.get(
        "volatile_atr_pct_threshold", DEFAULT_VOLATILE_ATR_PCT_THRESHOLD
    )
    storm_atr_pct_threshold = risk.get("storm_atr_pct_threshold", DEFAULT_STORM_ATR_PCT_THRESHOLD)
    volatile_size_multiplier = risk.get("volatile_size_multiplier", DEFAULT_VOLATILE_SIZE_MULTIPLIER)
    storm_size_multiplier = risk.get("storm_size_multiplier", DEFAULT_STORM_SIZE_MULTIPLIER)
    _require(
        isinstance(volatile_atr_pct_threshold, (int, float)) and volatile_atr_pct_threshold > 0,
        f"{prefix}.volatile_atr_pct_threshold must be positive",
    )
    _require(
        isinstance(storm_atr_pct_threshold, (int, float)) and storm_atr_pct_threshold > 0,
        f"{prefix}.storm_atr_pct_threshold must be positive",
    )
    _require(
        storm_atr_pct_threshold >= volatile_atr_pct_threshold,
        f"{prefix}.storm_atr_pct_threshold must be >= {prefix}.volatile_atr_pct_threshold",
    )
    _require(
        isinstance(volatile_size_multiplier, (int, float)) and 0 < volatile_size_multiplier <= 1,
        f"{prefix}.volatile_size_multiplier must be > 0 and <= 1",
    )
    _require(
        isinstance(storm_size_multiplier, (int, float)) and 0 <= storm_size_multiplier <= 1,
        f"{prefix}.storm_size_multiplier must be >= 0 and <= 1",
    )
    _require(
        storm_size_multiplier <= volatile_size_multiplier,
        f"{prefix}.storm_size_multiplier must be <= {prefix}.volatile_size_multiplier",
    )
    return {
        "max_loss_per_trade_pct": float(risk["max_loss_per_trade_pct"]),
        "max_trades_per_day": int(risk["max_trades_per_day"]),
        "volatile_atr_pct_threshold": float(volatile_atr_pct_threshold),
        "storm_atr_pct_threshold": float(storm_atr_pct_threshold),
        "volatile_size_multiplier": float(volatile_size_multiplier),
        "storm_size_multiplier": float(storm_size_multiplier),
    }


def _parse_exit(exit_config: Any, prefix: str) -> dict[str, str | float]:
    _require(isinstance(exit_config, dict), f"{prefix} must be object")
    _require(exit_config.get("stop") == "SWING_LOW", f"{prefix}.stop must be SWING_LOW")
    _require(
        isinstance(exit_config.get("take_profit_r_multiple"), (int, float))
        and exit_config["take_profit_r_multiple"] > 0,
        f"{prefix}.take_profit_r_multiple must be positive",
    )
    return {
        "stop": exit_config["stop"],
        "take_profit_r_multiple": float(exit_config["take_profit_r_multiple"]),
    }


def parse_config(data: Any) -> BotConfig:
    _require(isinstance(data, dict), "config/current must be an object")
    unknown_keys = set(data.keys()) - ALLOWED_TOP_LEVEL_KEYS
    _require(not unknown_keys, f"config/current has unknown keys: {sorted(unknown_keys)}")

    _require(isinstance(data.get("enabled"), bool), "enabled must be boolean")
    _require(data.get("network") == "mainnet-beta", "network must be 'mainnet-beta'")
    _require(data.get("pair") == "SOL/USDC", "pair must be 'SOL/USDC'")
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

    exit_config = _parse_exit(data.get("exit"), "exit")

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
        "strategy": strategy,
        "risk": risk,  # type: ignore[typeddict-item]
        "execution": {
            "mode": mode,
            "swap_provider": execution["swap_provider"],
            "slippage_bps": execution["slippage_bps"],
            "min_notional_usdc": float(execution["min_notional_usdc"]),
            "only_direct_routes": execution["only_direct_routes"],
        },
        "exit": exit_config,  # type: ignore[typeddict-item]
        "meta": {
            "config_version": meta["config_version"],
            "note": meta["note"],
        },
    }
    return parsed
