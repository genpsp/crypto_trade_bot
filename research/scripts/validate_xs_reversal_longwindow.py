"""③ 長期再検証: cross-sectional reversal が period-specific か（C と同じ手法）

Binance 15m(SOL/BTC/ETH ほか, 2022〜) で xs_rev を full＋年次＋2025-03 前後に分解。
C(funding carry)が 2025-26 限定だったのと同じく ③ も窓依存かを確定する。

Usage:
    python -m research.scripts.validate_xs_reversal_longwindow --assets BTC,ETH,SOL
"""

from __future__ import annotations

import argparse
import math
import statistics

from research.scripts.explore_track3_cross_sectional import (
    _Config, _aligned_panel, _annualization, _returns, _rebalance_returns, _sharpe,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="research/data/raw/binance15m")
    ap.add_argument("--assets", default="BTC,ETH,SOL")
    ap.add_argument("--cost-bps", type=float, default=0.0)
    args = ap.parse_args()

    labels = args.assets.split(",")
    paths = {lab: f"{args.dir}/{lab}USDT_15m.csv" for lab in labels}
    times, panel = _aligned_panel(paths)
    assets = labels
    rets = {a: _returns(panel[a]) for a in assets}
    print(f"[xs_rev] assets={assets} bars={len(times)} {times[0]}..{times[-1]} cost={args.cost_bps}bps\n")

    # 2025-03-01 境界の index
    boundary = next((i for i, t in enumerate(times) if t >= "2025-03-01"), len(times))
    # 年境界
    year_idx = {}
    for yr in ("2023", "2024", "2025", "2026"):
        idx = next((i for i, t in enumerate(times) if t >= f"{yr}-01-01"), None)
        if idx is not None:
            year_idx[yr] = idx

    configs = [_Config("xs_rev_L4_H4", "xs_reversal", 4, 4),
               _Config("xs_rev_L4_H16", "xs_reversal", 4, 16),
               _Config("xs_rev_L16_H4", "xs_reversal", 16, 4),
               _Config("xs_mom_L96_H48", "xs_momentum", 96, 48)]

    print("| config | full | 2023 | 2024 | 2025前(〜02) | 2025-03+ |")
    print("|---|---|---|---|---|---|")
    for cfg in configs:
        r = _rebalance_returns(cfg, times, panel, rets, assets, args.cost_bps)
        ann = _annualization(cfg.hold)

        def seg(t_lo, t_hi):
            sub = [val for k, val in enumerate(r)
                   if t_lo <= (cfg.lookback + k * cfg.hold) < t_hi]
            return _sharpe(sub) * ann if len(sub) >= 5 else float("nan")

        full = _sharpe(r) * ann
        y23 = seg(year_idx.get("2023", 0), year_idx.get("2024", len(times)))
        y24 = seg(year_idx.get("2024", 0), year_idx.get("2025", len(times)))
        pre25 = seg(year_idx.get("2025", 0), boundary)
        post25 = seg(boundary, len(times))
        print(f"| {cfg.name} | {full:+.2f} | {y23:+.2f} | {y24:+.2f} | {pre25:+.2f} | {post25:+.2f} |")


if __name__ == "__main__":
    main()
