"""Track ③ クロスセクション / 相対強弱 PoC

engine は単一資産なので research 側にマルチ資産ループを独立実装する
SOL/BTC/ETH(JPY) 15m を共通タイムスタンプで整合し long-short バスケットを評価

戦略:
- xs_momentum  : lookback L bar リターンで順位付け 最強 long / 最弱 short を H bar 保有
- xs_reversal  : 同順位で最強 short / 最弱 long（短期反転）
- baseline sol_buyhold : SOL long-only
- baseline sol_tsmom   : SOL 単体の時系列モメンタム（過去 L の符号）

判定（Done 基準）: バスケットの rolling Sharpe が単一 SOL を上回り DSR p < 0.10
コストは rebalance 時の turnover に cost_bps を課す（per unit notional, one-way）

Usage:
    python -m research.scripts.explore_track3_cross_sectional \
        --windows 13 --window-bars 2880 --cost-bps 7.0 \
        --output-json research/data/runs/track3_xs/soljpy_basket.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.src.eval.statistics import deflated_sharpe

_BARS_PER_YEAR_15M = 365 * 24 * 4  # 35040


def _load_closes(path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            out[row["open_time"]] = float(row["close"])
    return out


def _aligned_panel(paths: dict[str, str]) -> tuple[list[str], dict[str, list[float]]]:
    closes = {name: _load_closes(p) for name, p in paths.items()}
    common = set.intersection(*[set(c.keys()) for c in closes.values()])
    times = sorted(common)
    panel = {name: [closes[name][t] for t in times] for name in paths}
    return times, panel


def _returns(prices: list[float]) -> list[float]:
    return [0.0] + [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


@dataclass(frozen=True)
class _Config:
    name: str
    kind: str  # xs_momentum | xs_reversal | sol_buyhold | sol_tsmom
    lookback: int
    hold: int


def _rebalance_returns(
    cfg: _Config,
    times: list[str],
    panel: dict[str, list[float]],
    rets: dict[str, list[float]],
    assets: list[str],
    cost_bps: float,
) -> list[float]:
    """各 rebalance 期間（H bar）の純リターン列を返す（コスト控除後）"""
    n = len(times)
    L, H = cfg.lookback, cfg.hold
    cost = cost_bps / 10_000.0
    prev_w: dict[str, float] = {a: 0.0 for a in assets}
    out: list[float] = []
    t = L
    while t + H <= n:
        # シグナル算出（時刻 t の終値まで使用、保有は t..t+H）
        if cfg.kind in ("xs_momentum", "xs_reversal"):
            score = {a: panel[a][t] / panel[a][t - L] - 1.0 for a in assets}
            ranked = sorted(assets, key=lambda a: score[a])
            weak, strong = ranked[0], ranked[-1]
            if cfg.kind == "xs_momentum":
                w = {a: 0.0 for a in assets}; w[strong] = 1.0; w[weak] = -1.0
            else:
                w = {a: 0.0 for a in assets}; w[strong] = -1.0; w[weak] = 1.0
        elif cfg.kind == "sol_buyhold":
            w = {a: 0.0 for a in assets}; w["SOL"] = 1.0
        elif cfg.kind == "sol_tsmom":
            s = panel["SOL"][t] / panel["SOL"][t - L] - 1.0
            w = {a: 0.0 for a in assets}; w["SOL"] = 1.0 if s >= 0 else -1.0
        else:
            raise ValueError(cfg.kind)

        turnover = sum(abs(w[a] - prev_w[a]) for a in assets)
        # 保有期間の複利グロスリターン（重み固定で H bar 保有）
        gross = 0.0
        for a in assets:
            if w[a] == 0.0:
                continue
            compounded = 1.0
            for k in range(1, H + 1):
                compounded *= 1.0 + rets[a][t + k]
            gross += w[a] * (compounded - 1.0)
        out.append(gross - turnover * cost)
        prev_w = w
        t += H
    return out


def _annualization(hold: int) -> float:
    rebalances_per_year = _BARS_PER_YEAR_15M / hold
    return math.sqrt(rebalances_per_year)


def _sharpe(returns: list[float]) -> float:
    vals = [r for r in returns if math.isfinite(r)]
    if len(vals) < 2:
        return 0.0
    sd = statistics.pstdev(vals)
    return statistics.mean(vals) / sd if sd > 0 else 0.0


def _rolling_window_sharpes(returns: list[float], n_windows: int) -> list[float]:
    if not returns:
        return []
    size = max(1, len(returns) // n_windows)
    out = []
    for w in range(n_windows):
        seg = returns[w * size : (w + 1) * size]
        if len(seg) >= 2:
            out.append(_sharpe(seg))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sol", default="research/data/raw/soljpy_15m_to_2026_05.csv")
    parser.add_argument("--btc", default="research/data/raw/btcjpy_15m_1y.csv")
    parser.add_argument("--eth", default="research/data/raw/ethjpy_15m_1y.csv")
    parser.add_argument("--n-windows", type=int, default=13)
    parser.add_argument("--cost-bps", type=float, default=7.0)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    times, panel = _aligned_panel({"SOL": args.sol, "BTC": args.btc, "ETH": args.eth})
    assets = ["SOL", "BTC", "ETH"]
    rets = {a: _returns(panel[a]) for a in assets}
    print(f"[track3] aligned bars={len(times)}  {times[0]} .. {times[-1]}  cost_bps={args.cost_bps}")

    configs: list[_Config] = []
    for L in (4, 16, 48, 96):
        for H in (4, 16, 48):
            configs.append(_Config(f"xs_mom_L{L}_H{H}", "xs_momentum", L, H))
            configs.append(_Config(f"xs_rev_L{L}_H{H}", "xs_reversal", L, H))
    n_trials = len(configs)  # 多重検定補正
    # baseline（trial 数には含めない）
    baselines = [
        _Config("sol_buyhold_H16", "sol_buyhold", 1, 16),
        _Config("sol_tsmom_L16_H16", "sol_tsmom", 16, 16),
        _Config("sol_tsmom_L48_H16", "sol_tsmom", 48, 16),
    ]

    print(f"\n## Track ③ cross-sectional — n_trials(xs)={n_trials}\n")
    headers = ["config", "ann_Sharpe", "DSR_p", "roll_mean_Sh", "roll_min_Sh", "roll_pos%", "rebal", "tot_ret%"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")

    summary: list[dict[str, Any]] = []

    def evaluate(cfg: _Config, is_trial: bool) -> dict[str, Any]:
        r = _rebalance_returns(cfg, times, panel, rets, assets, args.cost_bps)
        ann = _sharpe(r) * _annualization(cfg.hold)
        dsr, dsr_p = deflated_sharpe(r, n_trials=n_trials if is_trial else 1)
        roll = _rolling_window_sharpes(r, args.n_windows)
        roll_ann = [s * _annualization(cfg.hold) for s in roll]
        tot = (math.prod(1.0 + x for x in r) - 1.0) * 100 if r else 0.0
        row = {
            "config": cfg.name,
            "kind": cfg.kind,
            "lookback": cfg.lookback,
            "hold": cfg.hold,
            "ann_sharpe": round(ann, 3),
            "dsr_p_value": round(dsr_p, 4),
            "roll_mean_sharpe": round(statistics.mean(roll_ann), 3) if roll_ann else 0.0,
            "roll_min_sharpe": round(min(roll_ann), 3) if roll_ann else 0.0,
            "roll_pos_pct": round(sum(1 for s in roll_ann if s > 0) / len(roll_ann) * 100, 1) if roll_ann else 0.0,
            "rebalances": len(r),
            "total_return_pct": round(tot, 2),
        }
        return row

    for cfg in baselines:
        row = evaluate(cfg, is_trial=False)
        summary.append(row)
        print("| " + " | ".join([row["config"], f"{row['ann_sharpe']:+.2f}", f"{row['dsr_p_value']:.3f}",
              f"{row['roll_mean_sharpe']:+.2f}", f"{row['roll_min_sharpe']:+.2f}", f"{row['roll_pos_pct']:.0f}",
              str(row["rebalances"]), f"{row['total_return_pct']:+.1f}"]) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    trial_rows = [evaluate(cfg, is_trial=True) for cfg in configs]
    for row in sorted(trial_rows, key=lambda x: -x["ann_sharpe"]):
        summary.append(row)
        print("| " + " | ".join([row["config"], f"{row['ann_sharpe']:+.2f}", f"{row['dsr_p_value']:.3f}",
              f"{row['roll_mean_sharpe']:+.2f}", f"{row['roll_min_sharpe']:+.2f}", f"{row['roll_pos_pct']:.0f}",
              str(row["rebalances"]), f"{row['total_return_pct']:+.1f}"]) + " |")

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(
                {
                    "aligned_bars": len(times),
                    "first": times[0],
                    "last": times[-1],
                    "cost_bps": args.cost_bps,
                    "n_trials": n_trials,
                    "results": summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[track3] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
