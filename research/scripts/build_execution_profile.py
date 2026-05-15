from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, UTC
import json
import statistics
from pathlib import Path
from typing import Any


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _direction(record: dict[str, Any]) -> str:
    raw = str(_first(record, "direction", "entry_direction", "side") or "LONG").upper()
    if raw in {"BUY", "LONG"}:
        return "LONG"
    if raw in {"SELL", "SHORT"}:
        return "SHORT"
    return raw


def _slippage_bps(record: dict[str, Any]) -> float | None:
    expected = _first(record, "expected_price", "expected_entry_price", "signal_price")
    actual = _first(record, "actual_fill_price", "fill_price", "entry_price")
    try:
        expected_f = float(expected)
        actual_f = float(actual)
    except Exception:
        return None
    if expected_f <= 0:
        return None
    if _direction(record) == "SHORT":
        return ((expected_f - actual_f) / expected_f) * 10_000
    return ((actual_f - expected_f) / expected_f) * 10_000


def _latency_seconds(record: dict[str, Any]) -> float | None:
    start = _parse_time(_first(record, "bar_close_time_iso", "signal_time", "created_at"))
    end = _parse_time(_first(record, "fill_time", "filled_at", "entry_time", "entry_time_iso"))
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def _is_rejected(record: dict[str, Any]) -> bool:
    state = str(_first(record, "state", "status", "order_state") or "").upper()
    reason = str(_first(record, "reason", "error", "close_reason") or "").upper()
    return state in {"FAILED", "CANCELED", "REJECTED"} or "REJECT" in reason or "FAIL" in reason


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "mean": 0.0, "std": 0.0, "p05": 0.0, "p95": 0.0}
    sorted_values = sorted(values)
    def pct(p: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        idx = (len(sorted_values) - 1) * p
        low = int(idx)
        high = min(len(sorted_values) - 1, low + 1)
        weight = idx - low
        return sorted_values[low] * (1 - weight) + sorted_values[high] * weight
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "p05": pct(0.05),
        "p95": pct(0.95),
    }


def build_profile(trades: list[dict[str, Any]], *, broker: str, pair: str) -> dict[str, Any]:
    slippage_by_direction: dict[str, list[float]] = defaultdict(list)
    latency_values: list[float] = []
    rejects = 0
    attempts = 0
    for trade in trades:
        attempts += 1
        if _is_rejected(trade):
            rejects += 1
        slippage = _slippage_bps(trade)
        if slippage is not None:
            slippage_by_direction[_direction(trade)].append(max(0.0, slippage))
        latency = _latency_seconds(trade)
        if latency is not None:
            latency_values.append(latency)
    by_direction = {
        direction: {"slippage_bps": _stats(values), "p_reject": rejects / attempts if attempts else 0.0}
        for direction, values in sorted(slippage_by_direction.items())
    }
    all_slippage = [value for values in slippage_by_direction.values() for value in values]
    return {
        "version": 1,
        "broker": broker,
        "pair": pair,
        "sample_count": attempts,
        "p_reject": rejects / attempts if attempts else 0.0,
        "latency_seconds": _stats(latency_values).get("mean", 0.0),
        "slippage_bps": _stats(all_slippage),
        "by_direction": by_direction,
    }


def _load_trades_from_firestore(*, model_id: str, mode: str, from_date_jst: str, to_date_jst: str) -> list[dict[str, Any]]:
    try:
        from google.cloud import firestore
    except Exception as error:  # pragma: no cover - optional integration path
        raise RuntimeError("google-cloud-firestore is required for Firestore extraction") from error
    client = firestore.Client()
    collection_name = "paper_trades" if mode.upper() == "PAPER" else "trades"
    from_date = datetime.fromisoformat(from_date_jst).date()
    to_date = datetime.fromisoformat(to_date_jst).date()
    trades: list[dict[str, Any]] = []
    cursor = from_date
    while cursor <= to_date:
        day = cursor.isoformat()
        items = client.collection("models").document(model_id).collection(collection_name).document(day).collection("items")
        for doc in items.stream():
            payload = doc.to_dict()
            if isinstance(payload, dict):
                payload.setdefault("trade_date", day)
                trades.append(payload)
        cursor = datetime.fromordinal(cursor.toordinal() + 1).date()
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Build stochastic execution profile from live/paper trade logs")
    parser.add_argument("--broker", required=True)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--since", default=None, help="metadata only; use --from-date-jst/--to-date-jst for Firestore extraction")
    parser.add_argument("--input", default=None, help="JSON file containing trade records")
    parser.add_argument("--model-id", default=None, help="Firestore model_id when --input is omitted")
    parser.add_argument("--mode", default="LIVE", choices=["LIVE", "PAPER"])
    parser.add_argument("--from-date-jst", default=None)
    parser.add_argument("--to-date-jst", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.input:
        trades = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if isinstance(trades, dict):
            trades = trades.get("trades", [])
    else:
        if not (args.model_id and args.from_date_jst and args.to_date_jst):
            raise ValueError("provide --input, or --model-id with --from-date-jst/--to-date-jst")
        trades = _load_trades_from_firestore(model_id=args.model_id, mode=args.mode, from_date_jst=args.from_date_jst, to_date_jst=args.to_date_jst)
    if not isinstance(trades, list):
        raise ValueError("input must be a list or {'trades': [...]} object")
    profile = build_profile([trade for trade in trades if isinstance(trade, dict)], broker=args.broker, pair=args.pair)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[research] execution profile written: {output}")


if __name__ == "__main__":
    main()
