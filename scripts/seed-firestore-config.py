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
    parser = argparse.ArgumentParser(description="Seed Firestore config/current")
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
            "note": "v0: spot swap only, long only, 2h close entry, TP=1.5R, small live test",
        },
    }


def main() -> int:
    args = parse_args()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required")

    if args.config_path:
        raw = json.loads(args.config_path.read_text(encoding="utf-8"))
        config = parse_config(raw)
    else:
        config = parse_config(build_default_config(args.mode))

    firestore = Client.from_service_account_json(credentials_path)
    firestore.document("config/current").set(config)
    source = str(args.config_path) if args.config_path else "built-in defaults"
    print(
        "Seeded config/current "
        f"with execution.mode={config['execution']['mode']} from {source}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] seed-firestore-config failed: {error}")
        raise SystemExit(1) from error
