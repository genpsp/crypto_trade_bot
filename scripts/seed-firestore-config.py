from __future__ import annotations

import argparse
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
    parser.add_argument("--mode", choices=["PAPER", "LIVE"], default="PAPER")
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
            "ema_fast_period": 12,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 0.5,
            "max_trades_per_day": 1,
        },
        "execution": {
            "mode": mode,
            "swap_provider": "JUPITER",
            "slippage_bps": 50,
            "min_notional_usdc": 30,
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

    config = parse_config(build_default_config(args.mode))
    firestore = Client.from_service_account_json(credentials_path)
    firestore.document("config/current").set(config)
    print(f"Seeded config/current with execution.mode={config['execution']['mode']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] seed-firestore-config failed: {error}")
        raise SystemExit(1) from error
