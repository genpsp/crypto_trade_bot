from __future__ import annotations

import argparse

from research.src.eval.statistics import power_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend minimum trade count for strategy selection")
    parser.add_argument("--win-rate", type=float, required=True, help="expected win rate; accepts 0.45 or 45")
    parser.add_argument("--r", type=float, required=True, help="target average R multiple for wins")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8)
    args = parser.parse_args()
    win_rate = args.win_rate / 100 if args.win_rate > 1 else args.win_rate
    recommended = power_analysis(win_rate, args.r, alpha=args.alpha, power=args.power)
    print(f"recommended N_min = {recommended}")


if __name__ == "__main__":
    main()
