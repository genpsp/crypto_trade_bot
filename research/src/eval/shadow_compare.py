from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _direction(record: dict[str, Any]) -> str:
    raw = _first(record, ("direction", "entry_direction", "side"))
    if str(raw).upper() in {"BUY", "LONG"}:
        return "LONG"
    if str(raw).upper() in {"SELL", "SHORT"}:
        return "SHORT"
    return str(raw or "UNKNOWN").upper()


def _entry_time(record: dict[str, Any]) -> datetime | None:
    return _parse_time(_first(record, ("entry_time", "entry_time_iso", "bar_close_time_iso", "created_at", "opened_at")))


def _pnl(record: dict[str, Any]) -> float:
    raw = _first(record, ("scaled_pnl_pct", "pnl_pct", "realized_pnl_pct", "pnl"))
    try:
        return float(raw or 0.0)
    except Exception:
        return 0.0


def _entry_price(record: dict[str, Any]) -> float | None:
    raw = _first(record, ("entry_price", "actual_fill_price", "fill_price", "open_price"))
    try:
        return float(raw) if raw is not None else None
    except Exception:
        return None


@dataclass(frozen=True)
class ShadowDiffSummary:
    live_count: int
    backtest_count: int
    matched_count: int
    trade_match_rate: float
    live_cumulative_pnl: float
    backtest_cumulative_pnl: float
    pnl_deviation_ratio: float | None
    threshold_breached: bool


def compare_trade_logs(
    *,
    live_trades: list[dict[str, Any]],
    backtest_trades: list[dict[str, Any]],
    max_time_delta_seconds: int = 1800,
    pnl_threshold: float = 0.20,
) -> dict[str, Any]:
    unmatched_backtest = set(range(len(backtest_trades)))
    matches: list[dict[str, Any]] = []
    live_only: list[dict[str, Any]] = []
    for live_index, live in enumerate(live_trades):
        live_time = _entry_time(live)
        live_direction = _direction(live)
        best_index: int | None = None
        best_delta: float | None = None
        for backtest_index in list(unmatched_backtest):
            candidate = backtest_trades[backtest_index]
            if _direction(candidate) not in {live_direction, "UNKNOWN"} and live_direction != "UNKNOWN":
                continue
            candidate_time = _entry_time(candidate)
            if live_time is None or candidate_time is None:
                continue
            delta = abs((live_time - candidate_time).total_seconds())
            if delta <= max_time_delta_seconds and (best_delta is None or delta < best_delta):
                best_delta = delta
                best_index = backtest_index
        if best_index is None:
            live_only.append({"live_index": live_index, "trade": live, "cause": "LOGIC"})
            continue
        unmatched_backtest.remove(best_index)
        backtest = backtest_trades[best_index]
        live_price = _entry_price(live)
        backtest_price = _entry_price(backtest)
        cause = "OK"
        price_diff_pct = None
        if live_price and backtest_price:
            price_diff_pct = abs(live_price - backtest_price) / abs(backtest_price)
            if price_diff_pct > 0.005:
                cause = "EXECUTION"
        matches.append(
            {
                "live_index": live_index,
                "backtest_index": best_index,
                "entry_time_delta_seconds": best_delta,
                "entry_price_diff_pct": price_diff_pct,
                "live_pnl": _pnl(live),
                "backtest_pnl": _pnl(backtest),
                "cause": cause,
            }
        )
    backtest_only = [{"backtest_index": index, "trade": backtest_trades[index], "cause": "DATA"} for index in sorted(unmatched_backtest)]
    live_pnl = sum(_pnl(trade) for trade in live_trades)
    backtest_pnl = sum(_pnl(trade) for trade in backtest_trades)
    pnl_deviation_ratio = None if backtest_pnl == 0 else (live_pnl - backtest_pnl) / abs(backtest_pnl)
    match_denominator = max(1, max(len(live_trades), len(backtest_trades)))
    match_rate = len(matches) / match_denominator
    threshold_breached = match_rate < 0.95 or (pnl_deviation_ratio is not None and abs(pnl_deviation_ratio) > pnl_threshold)
    summary = ShadowDiffSummary(
        live_count=len(live_trades),
        backtest_count=len(backtest_trades),
        matched_count=len(matches),
        trade_match_rate=match_rate,
        live_cumulative_pnl=live_pnl,
        backtest_cumulative_pnl=backtest_pnl,
        pnl_deviation_ratio=pnl_deviation_ratio,
        threshold_breached=threshold_breached,
    )
    cause_counts: dict[str, int] = {}
    for item in [*matches, *live_only, *backtest_only]:
        cause_counts[str(item.get("cause", "UNKNOWN"))] = cause_counts.get(str(item.get("cause", "UNKNOWN")), 0) + 1
    return {
        "summary": asdict(summary),
        "cause_counts": cause_counts,
        "matches": matches,
        "live_only": live_only,
        "backtest_only": backtest_only,
    }
