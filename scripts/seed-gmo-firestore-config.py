from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from google.cloud.firestore import Client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.gmo_bot.domain.model.types import BotConfig
from apps.gmo_bot.infra.config.firestore_config_repo import (
    GLOBAL_CONTROL_COLLECTION_ID,
    GLOBAL_CONTROL_DOC_ID,
    GLOBAL_CONTROL_PAUSE_FIELD,
    MODELS_COLLECTION_ID,
)
from apps.gmo_bot.infra.config.schema import parse_config


DEFAULT_MIN_NOTIONAL_JPY = 5_000
DEFAULT_MARGIN_USAGE_RATIO = 0.99
DEFAULT_LEVERAGE_MULTIPLIER = 1.0
DEFAULT_SLIPPAGE_BPS = 3
DEFAULT_MODEL_ID_PREFIX = "gmo_"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed Firestore model-scoped configs under "
            f"{MODELS_COLLECTION_ID}/{{model_id}}/config/current"
        )
    )
    parser.add_argument("--mode", choices=["PAPER", "LIVE"], default="LIVE")
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Path to a single GMO model config JSON to seed",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        help="Override model_id when --config-path is used",
    )
    parser.add_argument(
        "--control-only",
        action="store_true",
        help=(
            "Only seed "
            f"{GLOBAL_CONTROL_COLLECTION_ID}/{GLOBAL_CONTROL_DOC_ID} default fields and skip model config writes"
        ),
    )
    return parser.parse_args()



def _base_execution(mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "broker": "GMO_COIN",
        "slippage_bps": DEFAULT_SLIPPAGE_BPS,
        "min_notional_jpy": DEFAULT_MIN_NOTIONAL_JPY,
        "leverage_multiplier": DEFAULT_LEVERAGE_MULTIPLIER,
        "margin_usage_ratio": DEFAULT_MARGIN_USAGE_RATIO,
    }



def _default_long_config(mode: str) -> BotConfig:
    return parse_config(
        {
            "enabled": True,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "signal_timeframe": "2h",
            "strategy": {
                "name": "ema_trend_pullback_v0",
                "ema_fast_period": 5,
                "ema_slow_period": 13,
                "swing_low_lookback_bars": 6,
                "entry": "ON_BAR_CLOSE",
            },
            "risk": {
                "max_loss_per_trade_pct": 3.0,
                "max_trades_per_day": 2,
                "volatile_atr_pct_threshold": 1.3,
                "storm_atr_pct_threshold": 1.4,
                "volatile_size_multiplier": 0.75,
                "storm_size_multiplier": 0.5,
            },
            "execution": _base_execution(mode),
            "exit": {
                "stop": "SWING_LOW",
                "take_profit_r_multiple": 1.5,
            },
            "meta": {
                "config_version": 1,
                "note": "v0 ema pullback 2h long model for GMO Coin",
            },
        }
    )



def _default_short_config(mode: str) -> BotConfig:
    return parse_config(
        {
            "enabled": True,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "SHORT",
            "signal_timeframe": "2h",
            "strategy": {
                "name": "storm_short_v0",
                "ema_fast_period": 5,
                "ema_slow_period": 13,
                "swing_low_lookback_bars": 6,
                "entry": "ON_BAR_CLOSE",
            },
            "risk": {
                "max_loss_per_trade_pct": 3.0,
                "max_trades_per_day": 1,
                "volatile_atr_pct_threshold": 1.3,
                "storm_atr_pct_threshold": 1.4,
                "volatile_size_multiplier": 0.75,
                "storm_size_multiplier": 0.5,
            },
            "execution": _base_execution(mode),
            "exit": {
                "stop": "SWING_LOW",
                "take_profit_r_multiple": 1.5,
            },
            "meta": {
                "config_version": 1,
                "note": "v0 storm 2h short model for GMO Coin",
            },
        }
    )



def _default_15m_config(mode: str) -> BotConfig:
    return parse_config(
        {
            "enabled": False,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "BOTH",
            "signal_timeframe": "15m",
            "strategy": {
                "name": "ema_trend_pullback_15m_v0",
                "ema_fast_period": 9,
                "ema_slow_period": 34,
                "swing_low_lookback_bars": 12,
                "entry": "ON_BAR_CLOSE",
            },
            "risk": {
                "max_loss_per_trade_pct": 1.2,
                "max_trades_per_day": 4,
                "volatile_atr_pct_threshold": 0.9,
                "storm_atr_pct_threshold": 1.4,
                "volatile_size_multiplier": 0.7,
                "storm_size_multiplier": 0.35,
            },
            "execution": _base_execution(mode),
            "exit": {
                "stop": "SWING_LOW",
                "take_profit_r_multiple": 1.8,
            },
            "meta": {
                "config_version": 1,
                "note": "v0 ema pullback 15m both model for GMO Coin",
            },
        }
    )



def build_default_model_configs(mode: str) -> dict[str, BotConfig]:
    return {
        f"{DEFAULT_MODEL_ID_PREFIX}ema_pullback_2h_long_v0": _default_long_config(mode),
        f"{DEFAULT_MODEL_ID_PREFIX}storm_2h_short_v0": _default_short_config(mode),
        f"{DEFAULT_MODEL_ID_PREFIX}ema_pullback_15m_both_v0": _default_15m_config(mode),
    }



def _normalize_model_id(model_id: str) -> str:
    stripped = model_id.strip()
    if stripped.startswith(DEFAULT_MODEL_ID_PREFIX):
        return stripped
    return f"{DEFAULT_MODEL_ID_PREFIX}{stripped}"


def _infer_model_id_from_path(config_path: Path) -> str | None:
    parts = list(config_path.parts)
    for index, part in enumerate(parts):
        if part in {"gmo_models", "models"} and index + 1 < len(parts):
            return parts[index + 1]
    return None



def load_single_model_config(config_path: Path, model_id_override: str | None) -> tuple[str, BotConfig]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config payload must be object: {config_path}")
    if "models" in raw:
        raise ValueError("models key is no longer supported in config JSON")

    config = parse_config(raw)
    model_id = model_id_override or _infer_model_id_from_path(config_path)
    if not model_id:
        raise ValueError("model_id could not be inferred, pass --model-id")
    return _normalize_model_id(model_id), config



def _build_model_doc_payload(model_id: str, config: BotConfig) -> dict[str, object]:
    return {
        "model_id": model_id,
        "enabled": config["enabled"],
        "mode": config["execution"]["mode"],
        "direction": config["direction"],
        "broker": config["broker"],
        "pair": config["pair"],
    }



def seed_global_control_defaults(firestore: Client) -> bool:
    control_ref = firestore.collection(GLOBAL_CONTROL_COLLECTION_ID).document(GLOBAL_CONTROL_DOC_ID)
    snapshot = control_ref.get()
    if not snapshot.exists:
        control_ref.set({GLOBAL_CONTROL_PAUSE_FIELD: False}, merge=True)
        return True

    payload = snapshot.to_dict()
    if not isinstance(payload, dict) or not isinstance(payload.get(GLOBAL_CONTROL_PAUSE_FIELD), bool):
        control_ref.set({GLOBAL_CONTROL_PAUSE_FIELD: False}, merge=True)
        return True

    return False



def _build_model_config_payload(config: BotConfig) -> dict[str, object]:
    execution = dict(config["execution"])
    execution.pop("mode", None)
    return {
        "broker": config["broker"],
        "pair": config["pair"],
        "signal_timeframe": config["signal_timeframe"],
        "strategy": config["strategy"],
        "risk": config["risk"],
        "execution": execution,
        "exit": config["exit"],
        "meta": config["meta"],
    }



def seed_model_config(firestore: Client, model_id: str, config: BotConfig) -> None:
    model_ref = firestore.document(f"{MODELS_COLLECTION_ID}/{model_id}")
    model_ref.set(_build_model_doc_payload(model_id, config), merge=True)
    model_ref.collection("config").document("current").set(_build_model_config_payload(config))



def main() -> int:
    args = parse_args()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required")

    firestore = Client.from_service_account_json(credentials_path)
    seeded_control = seed_global_control_defaults(firestore)
    if args.control_only:
        print(f"Seeded GMO global control defaults (global_control_seeded={seeded_control})")
        return 0

    if args.config_path:
        model_id, config = load_single_model_config(args.config_path, args.model_id)
        seed_model_config(firestore, model_id, config)
        print(
            "Seeded GMO Firestore model config "
            f"(model_id={model_id}, mode={config['execution']['mode']}) from {args.config_path}; "
            f"global_control_seeded={seeded_control}"
        )
        return 0

    seeded_model_ids: list[str] = []
    for model_id, config in build_default_model_configs(args.mode).items():
        seed_model_config(firestore, model_id, config)
        seeded_model_ids.append(model_id)

    print(
        "Seeded GMO Firestore model configs "
        f"(models={seeded_model_ids}, mode={args.mode}, global_control_seeded={seeded_control})"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] seed-gmo-firestore-config failed: {error}")
        raise SystemExit(1) from error
