"""Compare LIVE trades across two strategy variants / config versions.

Used post-cutover (or any time) to attribute LIVE performance to specific
strategy versions. Splits trades into two groups (by ``variant_id`` or
``config_version``) and emits side-by-side metrics plus a bootstrap CI on
the mean-PnL difference.

Input modes:
- ``--trades-json file_a.json file_b.json``: read pre-dumped JSON arrays
  (use ``dump_live_trades_for_profile`` to produce these)
- ``--firestore --model-id ... --from-date-jst ... --to-date-jst ...``:
  read directly from Firestore (requires google-cloud-firestore)

Group selection:
- ``--variant-a v0 --variant-b v2_dir_session_vol_time120`` filters by
  ``variant_id`` field. Trades without ``variant_id`` are treated as
  ``v0`` by convention (pre-cutover era).
- ``--config-version-a 1 --config-version-b 2`` filters by ``config_version``.

Usage:

    # From dumped JSON
    python -m research.scripts.compare_live_versions \\
        --trades-json research/data/execution_profiles/raw_trades/live_pre.json \\
                       research/data/execution_profiles/raw_trades/live_post.json \\
        --variant-a v0_baseline --variant-b v2_dir_session_vol_time120

    # From Firestore directly
    python -m research.scripts.compare_live_versions \\
        --firestore --model-id gmo_ema_pullback_15m_both_v0 --mode LIVE \\
        --from-date-jst 2026-04-21 --to-date-jst 2026-06-20 \\
        --config-version-a 1 --config-version-b 2
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


# Reuse existing realized-PnL computation
from apps.gmo_bot.infra.alerting.daily_trade_summary import _compute_trade_realized_pnl_jpy


def _load_trades_json(paths: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        payload = json.loads(Path(p).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("trades") or payload.get("items") or []
        if not isinstance(payload, list):
            raise ValueError(f"expected list payload: {p}")
        out.extend(item for item in payload if isinstance(item, dict))
    return out


def _load_firestore_trades(
    *, model_id: str, mode: str, from_date_jst: str, to_date_jst: str
) -> list[dict[str, Any]]:
    try:
        from google.cloud import firestore
    except Exception as error:  # pragma: no cover
        raise RuntimeError("google-cloud-firestore is required") from error
    client = firestore.Client()
    collection_name = "paper_trades" if mode.upper() == "PAPER" else "trades"
    from_date = datetime.fromisoformat(from_date_jst).date()
    to_date = datetime.fromisoformat(to_date_jst).date()
    out: list[dict[str, Any]] = []
    cursor = from_date
    from datetime import timedelta

    while cursor <= to_date:
        day = cursor.isoformat()
        items = (
            client.collection("models")
            .document(model_id)
            .collection(collection_name)
            .document(day)
            .collection("items")
            .stream()
        )
        for doc in items:
            data = doc.to_dict() or {}
            if not isinstance(data, dict):
                continue
            data.setdefault("trade_id", doc.id)
            out.append(data)
        cursor = cursor + timedelta(days=1)
    return out


def _split_trades(
    trades: list[dict[str, Any]],
    *,
    variant_a: str | None,
    variant_b: str | None,
    config_version_a: int | None,
    config_version_b: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    a: list[dict[str, Any]] = []
    b: list[dict[str, Any]] = []
    for t in trades:
        if variant_a is not None or variant_b is not None:
            v = str(t.get("variant_id") or "v0_baseline")
            if variant_a is not None and v == variant_a:
                a.append(t)
            elif variant_b is not None and v == variant_b:
                b.append(t)
        elif config_version_a is not None or config_version_b is not None:
            v = t.get("config_version")
            if config_version_a is not None and v == config_version_a:
                a.append(t)
            elif config_version_b is not None and v == config_version_b:
                b.append(t)
    return a, b


def _is_closed(trade: dict[str, Any]) -> bool:
    state = str(trade.get("state") or "")
    if state and state != "CLOSED":
        return False
    return _compute_trade_realized_pnl_jpy(trade) is not None


def _holding_bars(trade: dict[str, Any]) -> int | None:
    """Approximate holding period from entry/exit timestamps (15m bars assumed)."""
    pos = trade.get("position") or {}
    if not isinstance(pos, dict):
        return None
    entry = pos.get("entry_time_iso") or trade.get("bar_close_time_iso")
    exit_ = pos.get("exit_time_iso")
    if not entry or not exit_:
        return None
    try:
        e = datetime.fromisoformat(str(entry).replace("Z", "+00:00"))
        x = datetime.fromisoformat(str(exit_).replace("Z", "+00:00"))
    except ValueError:
        return None
    delta_min = (x - e).total_seconds() / 60
    return int(delta_min / 15)  # 15m bars


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [t for t in trades if _is_closed(t)]
    if not closed:
        return {"n": 0}
    pnls = [_compute_trade_realized_pnl_jpy(t) or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    holdings = [h for h in (_holding_bars(t) for t in closed) if h is not None]
    pnls_sorted = sorted(pnls)
    n = len(pnls)

    def p(arr: list[float], q: float) -> float:
        idx = max(0, min(len(arr) - 1, int(q * len(arr) / 100)))
        return arr[idx]

    # Max consecutive losses
    streak = 0
    max_loss_streak = 0
    for v in pnls:
        if v < 0:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0

    # LONG/SHORT split
    longs = [t for t in closed if str(t.get("direction")) == "LONG"]
    shorts = [t for t in closed if str(t.get("direction")) == "SHORT"]
    long_pnls = [_compute_trade_realized_pnl_jpy(t) or 0.0 for t in longs]
    short_pnls = [_compute_trade_realized_pnl_jpy(t) or 0.0 for t in shorts]

    return {
        "n": n,
        "wr_pct": round(100 * len(wins) / n, 2),
        "mean_pnl_jpy": round(sum(pnls) / n, 2),
        "sum_pnl_jpy": round(sum(pnls), 2),
        "median_pnl_jpy": round(statistics.median(pnls), 2),
        "p05_pnl_jpy": round(p(pnls_sorted, 5), 2),
        "p95_pnl_jpy": round(p(pnls_sorted, 95), 2),
        "stdev_pnl_jpy": round(statistics.stdev(pnls), 2) if n > 1 else 0.0,
        "avg_win_jpy": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_jpy": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_consecutive_losses": max_loss_streak,
        "avg_holding_bars": round(sum(holdings) / len(holdings), 1) if holdings else 0.0,
        "long_n": len(longs),
        "long_wr_pct": round(
            100 * sum(1 for v in long_pnls if v > 0) / len(long_pnls), 2
        )
        if long_pnls
        else 0.0,
        "long_sum_pnl_jpy": round(sum(long_pnls), 2),
        "short_n": len(shorts),
        "short_wr_pct": round(
            100 * sum(1 for v in short_pnls if v > 0) / len(short_pnls), 2
        )
        if short_pnls
        else 0.0,
        "short_sum_pnl_jpy": round(sum(short_pnls), 2),
    }


def _bootstrap_diff_ci(
    a_pnls: list[float],
    b_pnls: list[float],
    *,
    iterations: int = 5000,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrap CI on mean(B) - mean(A) without external deps."""
    if not a_pnls or not b_pnls:
        return {}
    rng = random.Random(seed)
    diffs: list[float] = []
    n_a = len(a_pnls)
    n_b = len(b_pnls)
    for _ in range(iterations):
        sa = sum(rng.choice(a_pnls) for _ in range(n_a)) / n_a
        sb = sum(rng.choice(b_pnls) for _ in range(n_b)) / n_b
        diffs.append(sb - sa)
    diffs.sort()

    def q(arr: list[float], qp: float) -> float:
        return arr[max(0, min(len(arr) - 1, int(qp * len(arr) / 100)))]

    observed = sum(b_pnls) / n_b - sum(a_pnls) / n_a
    p_greater = sum(1 for d in diffs if d > 0) / len(diffs)
    return {
        "observed_diff": round(observed, 3),
        "ci_p05": round(q(diffs, 5), 3),
        "ci_p50": round(q(diffs, 50), 3),
        "ci_p95": round(q(diffs, 95), 3),
        "fraction_positive_diff": round(p_greater, 3),
    }


def _render_side_by_side(
    label_a: str, m_a: dict, label_b: str, m_b: dict, boot: dict
) -> str:
    keys = [
        "n",
        "wr_pct",
        "mean_pnl_jpy",
        "sum_pnl_jpy",
        "median_pnl_jpy",
        "p05_pnl_jpy",
        "p95_pnl_jpy",
        "stdev_pnl_jpy",
        "avg_win_jpy",
        "avg_loss_jpy",
        "max_consecutive_losses",
        "avg_holding_bars",
        "long_n",
        "long_wr_pct",
        "long_sum_pnl_jpy",
        "short_n",
        "short_wr_pct",
        "short_sum_pnl_jpy",
    ]
    lines = [
        "| metric | " + label_a + " | " + label_b + " | delta |",
        "|---|---:|---:|---:|",
    ]
    for k in keys:
        va = m_a.get(k, "-")
        vb = m_b.get(k, "-")
        delta = ""
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d = vb - va
            delta = f"{d:+.2f}" if isinstance(d, float) else f"{d:+d}"
        lines.append(f"| {k} | {va} | {vb} | {delta} |")
    if boot:
        lines.append("")
        lines.append("### Bootstrap CI on mean PnL difference (B - A)")
        lines.append("")
        lines.append(
            f"- observed: **{boot['observed_diff']:+.2f}** JPY/trade  "
            f"(B mean - A mean)"
        )
        lines.append(
            f"- 90% CI: [{boot['ci_p05']:+.2f}, {boot['ci_p95']:+.2f}]"
        )
        lines.append(
            f"- fraction of bootstrap samples with positive diff: "
            f"**{boot['fraction_positive_diff'] * 100:.1f}%**  "
            f"(≥95% → strong evidence B > A)"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trades-json",
        nargs="*",
        default=None,
        help="One or more JSON files containing trade arrays",
    )
    parser.add_argument("--firestore", action="store_true")
    parser.add_argument("--model-id", default="gmo_ema_pullback_15m_both_v0")
    parser.add_argument("--mode", default="LIVE", choices=["LIVE", "PAPER"])
    parser.add_argument("--from-date-jst", default=None)
    parser.add_argument("--to-date-jst", default=None)
    parser.add_argument("--variant-a", default=None)
    parser.add_argument("--variant-b", default=None)
    parser.add_argument("--config-version-a", type=int, default=None)
    parser.add_argument("--config-version-b", type=int, default=None)
    parser.add_argument(
        "--label-a",
        default=None,
        help="Display label for group A (defaults to variant/version)",
    )
    parser.add_argument("--label-b", default=None)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    parser.add_argument("--output", default=None, help="Markdown output path")
    args = parser.parse_args()

    if args.firestore:
        if not args.from_date_jst or not args.to_date_jst:
            raise SystemExit(
                "--from-date-jst and --to-date-jst are required for --firestore"
            )
        trades = _load_firestore_trades(
            model_id=args.model_id,
            mode=args.mode,
            from_date_jst=args.from_date_jst,
            to_date_jst=args.to_date_jst,
        )
    elif args.trades_json:
        trades = _load_trades_json(args.trades_json)
    else:
        raise SystemExit("Either --trades-json or --firestore is required")

    if (args.variant_a is None and args.variant_b is None
            and args.config_version_a is None and args.config_version_b is None):
        raise SystemExit(
            "Provide --variant-a/--variant-b or --config-version-a/--config-version-b"
        )

    a, b = _split_trades(
        trades,
        variant_a=args.variant_a,
        variant_b=args.variant_b,
        config_version_a=args.config_version_a,
        config_version_b=args.config_version_b,
    )

    label_a = (
        args.label_a
        or args.variant_a
        or (f"config_v{args.config_version_a}" if args.config_version_a else "A")
    )
    label_b = (
        args.label_b
        or args.variant_b
        or (f"config_v{args.config_version_b}" if args.config_version_b else "B")
    )

    print(
        f"Loaded {len(trades)} trades total; "
        f"group {label_a}={len(a)}, group {label_b}={len(b)}"
    )

    m_a = _metrics(a)
    m_b = _metrics(b)

    # Bootstrap on per-trade PnL
    pnls_a = [
        _compute_trade_realized_pnl_jpy(t) or 0.0 for t in a if _is_closed(t)
    ]
    pnls_b = [
        _compute_trade_realized_pnl_jpy(t) or 0.0 for t in b if _is_closed(t)
    ]
    boot = _bootstrap_diff_ci(pnls_a, pnls_b, iterations=args.bootstrap_iterations)

    md = (
        f"# LIVE version comparison\n\n"
        f"- Group A: **{label_a}** (n_closed={m_a.get('n', 0)})\n"
        f"- Group B: **{label_b}** (n_closed={m_b.get('n', 0)})\n\n"
        + _render_side_by_side(label_a, m_a, label_b, m_b, boot)
        + "\n"
    )

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"\nWrote: {args.output}")
    print()
    print(md)


if __name__ == "__main__":
    main()
