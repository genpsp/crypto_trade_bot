"""Track ②（regime-switch meta）/ ④（BTC lead-lag entry）評価スクリプト

new_edge_plan の Track ②/④ を rolling 13×3000 窓 / ideal_v1 で評価する
比較対象:
- ema_v2_baseline      : LIVE 構成（directional_session+vol+time120）= 現行 v2 を同枠で再計算
- mean_reversion_single: MR 単体（null gate / FixedR）— Phase3-V で REJECT 済の参照
- router_live_bundle   : regime_router + LIVE bundle（trend=v2 / chop=MR 排他）
- router_null_bundle   : regime_router + null gate / FixedR（純ルーティング）
- btc_leadlag_0_3 / 0_5: BTC lead-lag entry（閾値 0.3 / 0.5）

Usage:
    python -m research.scripts.explore_track2_4_meta \
        --bars research/data/raw/soljpy_15m_to_2026_05.csv \
        --windows 13 --window-bars 3000 \
        --output-json research/data/runs/track2_4_meta/soljpy_15m.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config


# MR / router で使う mean-reversion パラメータ（Phase3-V 既定 + chop 寄せ）
_MR_PARAMS: dict[str, Any] = {
    "bb_period": 20,
    "bb_num_std": 2.0,
    "adx_period": 14,
    "adx_chop_max": 25.0,
    "stop_atr_cushion": 0.5,
    "long_atr_pct_max": 1.5,
    "short_atr_pct_max": 1.5,
}

# router の trend/chop 分割閾値（MR の adx_chop_max と揃える）
_ROUTER_PARAMS: dict[str, Any] = {
    "router_adx_period": 14,
    "router_adx_trend_min": 25.0,
}

_LIVE_COMPONENTS: dict[str, Any] = {
    "regime_gate": {
        "type": "composite",
        "gates": [
            {
                "type": "directional_session",
                "long_allowed_utc_hours": [15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8],
                "short_allowed_utc_hours": [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
            },
            {"type": "volume_confirmed", "period": 20, "volume_multiplier": 0.4},
        ],
    },
    "exit_policy": {"type": "time_exit", "max_holding_bars": 120, "prefer_breakeven": False},
}


@dataclass(frozen=True)
class _Variant:
    name: str
    strategy_name: str
    components: dict[str, Any] | None  # None → no components key（= v0 既定 FixedR/null）
    extra_strategy_params: dict[str, Any] = field(default_factory=dict)


def _build_variants() -> list[_Variant]:
    return [
        _Variant("ema_v2_baseline", "ema_trend_pullback_15m_v2", components=_LIVE_COMPONENTS),
        _Variant("mean_reversion_single", "mean_reversion_15m_v0", components=None, extra_strategy_params=_MR_PARAMS),
        _Variant(
            "router_live_bundle",
            "regime_router_15m_v0",
            components=_LIVE_COMPONENTS,
            extra_strategy_params={**_MR_PARAMS, **_ROUTER_PARAMS},
        ),
        _Variant(
            "router_null_bundle",
            "regime_router_15m_v0",
            components=None,
            extra_strategy_params={**_MR_PARAMS, **_ROUTER_PARAMS},
        ),
        _Variant(
            "btc_leadlag_0_3",
            "btc_leadlag_15m_v0",
            components=None,
            extra_strategy_params={"btc_min_abs_return_pct": 0.3, "btc_lookback_bars": 4},
        ),
        _Variant(
            "btc_leadlag_0_5",
            "btc_leadlag_15m_v0",
            components=None,
            extra_strategy_params={"btc_min_abs_return_pct": 0.5, "btc_lookback_bars": 4},
        ),
    ]


def _make_config(base: dict[str, Any], variant: _Variant, pair: str) -> dict[str, Any]:
    config = json.loads(json.dumps(base))
    config["pair"] = pair
    config["signal_timeframe"] = "15m"
    config["execution"] = dict(config.get("execution", {}))
    config["execution"]["model_id"] = "ideal_v1"
    config["strategy"] = dict(config["strategy"])
    config["strategy"]["name"] = variant.strategy_name
    for key, value in variant.extra_strategy_params.items():
        config["strategy"][key] = value
    if variant.components is not None:
        config["strategy"]["components"] = variant.components
    else:
        config["strategy"].pop("components", None)
    return config


def _evaluate_window(variant: _Variant, bars, base_config, pair: str) -> dict[str, Any]:
    config = _make_config(base_config, variant, pair)
    report = run_backtest(bars=bars, config=config)
    closed = [t for t in report.trades if t.exit_reason != "OPEN"]
    sum_scaled = sum(t.scaled_pnl_pct or 0.0 for t in closed)
    wins = sum(1 for t in closed if (t.pnl_pct or 0) > 0)
    wr = (wins / len(closed) * 100) if closed else 0.0
    # router の regime 内訳
    regime_counts: dict[str, int] = {}
    for t in closed:
        diag = getattr(t, "entry_regime", None) or {}
        reg = diag.get("router_regime") if isinstance(diag, dict) else None
        if reg:
            regime_counts[reg] = regime_counts.get(reg, 0) + 1
    return {
        "scaled_pnl_pct": round(sum_scaled, 4),
        "trades": len(closed),
        "win_rate_pct": round(wr, 2),
        "regime_counts": regime_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="research/data/raw/soljpy_15m_to_2026_05.csv")
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument("--pair", default="SOL/JPY")
    parser.add_argument("--windows", type=int, default=13)
    parser.add_argument("--window-bars", type=int, default=3000)
    parser.add_argument("--variants", nargs="*", default=None)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    base_config = load_bot_config(args.base_config)
    all_bars = read_bars_from_csv(args.bars)
    total_needed = args.windows * args.window_bars
    if total_needed > len(all_bars):
        raise SystemExit(f"need {total_needed} bars but CSV has {len(all_bars)}")
    all_bars = all_bars[-total_needed:]
    attach_regime_tags(all_bars)

    variants = _build_variants()
    if args.variants:
        wanted = set(args.variants)
        variants = [v for v in variants if v.name in wanted]

    per_variant: dict[str, list[dict[str, Any]]] = {v.name: [] for v in variants}
    for w in range(args.windows):
        bars = all_bars[w * args.window_bars : (w + 1) * args.window_bars]
        if len(bars) < args.window_bars:
            break
        for variant in variants:
            row = _evaluate_window(variant, bars, base_config, args.pair)
            row["window_index"] = w
            per_variant[variant.name].append(row)

    print(f"\n## Track ②/④ meta — pair={args.pair} windows={args.windows} window_bars={args.window_bars}\n")
    headers = ["variant"] + [f"w{i}" for i in range(args.windows)] + ["min", "mean", "pos%", "trades", "WR%"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")

    summary: list[dict[str, Any]] = []
    for variant in variants:
        rows = per_variant[variant.name]
        pnls = [r["scaled_pnl_pct"] for r in rows]
        if not pnls:
            continue
        win_min = min(pnls)
        win_mean = sum(pnls) / len(pnls)
        pos_rate = sum(1 for v in pnls if v > 0) / len(pnls) * 100
        tot_trades = sum(r["trades"] for r in rows)
        tot_wins = sum(round(r["win_rate_pct"] / 100 * r["trades"]) for r in rows)
        wr = (tot_wins / tot_trades * 100) if tot_trades else 0.0
        regime_total: dict[str, int] = {}
        for r in rows:
            for k, v in r["regime_counts"].items():
                regime_total[k] = regime_total.get(k, 0) + v
        cells = (
            [variant.name]
            + [f"{v:+.2f}" for v in pnls]
            + [f"{win_min:+.2f}", f"{win_mean:+.2f}", f"{pos_rate:.0f}", str(tot_trades), f"{wr:.1f}"]
        )
        print("| " + " | ".join(cells) + " |")
        summary.append(
            {
                "variant": variant.name,
                "per_window": rows,
                "min": round(win_min, 4),
                "mean": round(win_mean, 4),
                "pos_rate_pct": round(pos_rate, 2),
                "total_trades": tot_trades,
                "win_rate_pct": round(wr, 2),
                "regime_counts": regime_total,
            }
        )
        if regime_total:
            print(f"|   ↳ regime split: {regime_total} | | | | | | | | | | | | | | | | | |")

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(
                {
                    "pair": args.pair,
                    "windows": args.windows,
                    "window_bars": args.window_bars,
                    "bars_path": args.bars,
                    "variants": summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[track2_4] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
