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

from pybot.infra.config.schema import parse_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed Firestore model-scoped configs under models/{model_id}/config/current"
    )
    parser.add_argument("--mode", choices=["PAPER", "LIVE"], default="LIVE")
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Path to JSON config to seed instead of built-in defaults",
    )
    return parser.parse_args()


def build_default_config(mode: str) -> dict:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG_ONLY",
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
        "execution": {
            "mode": mode,
            "swap_provider": "JUPITER",
            "slippage_bps": 15,
            "min_notional_usdc": 20,
            "only_direct_routes": False,
        },
        "exit": {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 1.5,
        },
        "meta": {
            "config_version": 2,
            "note": "v0: spot swap only, 2h close entry, core long + optional storm short",
        },
        "models": [
            {
                "model_id": "core_long_v0",
                "enabled": True,
                "direction": "LONG_ONLY",
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
                "exit": {
                    "stop": "SWING_LOW",
                    "take_profit_r_multiple": 1.5,
                },
            },
            {
                "model_id": "storm_short_v0",
                "enabled": False,
                "direction": "SHORT_ONLY",
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
                "exit": {
                    "stop": "SWING_LOW",
                    "take_profit_r_multiple": 1.5,
                },
            },
        ],
    }


def discover_model_configs(config_path: Path) -> list[dict]:
    models_root = config_path.parent.parent / "models"
    if not models_root.exists():
        return []

    discovered: list[dict] = []
    for model_config_path in sorted(models_root.glob("*/config/current.json")):
        payload = json.loads(model_config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            discovered.append(payload)
    return discovered


def split_global_and_models(config: dict) -> tuple[dict, list[dict]]:
    global_config = dict(config)
    raw_models = global_config.pop("models", [])
    models = [item for item in raw_models if isinstance(item, dict)]
    return global_config, models


def seed_firestore_config(firestore: Client, config: dict) -> tuple[dict, int]:
    global_config, model_configs = split_global_and_models(config)
    execution_config = dict(global_config["execution"])
    execution_mode = execution_config.pop("mode")

    for model_config in model_configs:
        model_id = model_config["model_id"]
        model_wallet_key_path = model_config.get("wallet_key_path")
        model_scoped_config = {
            "network": global_config["network"],
            "pair": global_config["pair"],
            "signal_timeframe": global_config["signal_timeframe"],
            "strategy": model_config["strategy"],
            "risk": model_config["risk"],
            "execution": dict(execution_config),
            "exit": model_config["exit"],
            "meta": global_config["meta"],
        }
        model_doc_payload = {
            "model_id": model_id,
            "enabled": model_config["enabled"],
            "direction": model_config["direction"],
            "mode": execution_mode,
        }
        if isinstance(model_wallet_key_path, str) and model_wallet_key_path.strip() != "":
            model_doc_payload["wallet_key_path"] = model_wallet_key_path.strip()
        firestore.document(f"models/{model_id}").set(model_doc_payload)
        firestore.document(f"models/{model_id}/config/current").set(model_scoped_config)

    return global_config, len(model_configs)


def main() -> int:
    args = parse_args()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required")

    if args.config_path:
        raw = json.loads(args.config_path.read_text(encoding="utf-8"))
        if "models" not in raw:
            discovered_models = discover_model_configs(args.config_path)
            if discovered_models:
                raw["models"] = discovered_models
        config = parse_config(raw)
    else:
        config = parse_config(build_default_config(args.mode))

    firestore = Client.from_service_account_json(credentials_path)
    global_config, model_count = seed_firestore_config(firestore, config)
    source = str(args.config_path) if args.config_path else "built-in defaults"
    print(
        "Seeded Firestore model configs "
        f"(models={model_count}, global config/current is not used) "
        f"with execution.mode={global_config['execution']['mode']} from {source}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] seed-firestore-config failed: {error}")
        raise SystemExit(1) from error
