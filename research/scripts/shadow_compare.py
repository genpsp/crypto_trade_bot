from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib import request

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.domain.backtest_engine import run_backtest
from research.src.eval.shadow_compare import compare_trade_logs
from research.src.infra.research_config import load_bot_config


def _load_json_list(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("trades", [])
    if not isinstance(payload, list):
        raise ValueError(f"expected list payload: {path}")
    return [item for item in payload if isinstance(item, dict)]


def _load_firestore_trades(*, model_id: str, mode: str, from_date_jst: str, to_date_jst: str) -> list[dict[str, Any]]:
    try:
        from google.cloud import firestore
    except Exception as error:  # pragma: no cover - optional integration path
        raise RuntimeError("google-cloud-firestore is required for Firestore extraction") from error
    client = firestore.Client()
    collection_name = "paper_trades" if mode.upper() == "PAPER" else "trades"
    from_date = datetime.fromisoformat(from_date_jst).date()
    to_date = datetime.fromisoformat(to_date_jst).date()
    cursor = from_date
    trades: list[dict[str, Any]] = []
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


def _post_slack(webhook_url: str, payload: dict[str, Any]) -> None:
    body = json.dumps({"text": "research shadow_compare threshold breached\n```" + json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2) + "```"}).encode("utf-8")
    req = request.Request(webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    request.urlopen(req, timeout=10).read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare LIVE/PAPER trades with same-period backtest trades")
    parser.add_argument("--broker", required=True)
    parser.add_argument("--since", default="7d")
    parser.add_argument("--live-trades-json", default=None, help="JSON list exported from Firestore")
    parser.add_argument("--model-id", default=None, help="Firestore model_id when --live-trades-json is omitted")
    parser.add_argument("--mode", default="LIVE", choices=["LIVE", "PAPER"])
    parser.add_argument("--from-date-jst", default=None)
    parser.add_argument("--to-date-jst", default=None)
    parser.add_argument("--backtest-trades-json", default=None, help="precomputed backtest trades JSON")
    parser.add_argument("--config", default=None, help="config path used to run shadow backtest")
    parser.add_argument("--bars-path", default=None, help="OHLCV CSV used to run shadow backtest")
    parser.add_argument("--output", default=None)
    parser.add_argument("--slack-webhook-url", default=None)
    parser.add_argument("--slack-on-threshold", type=float, default=None, help="PnL deviation threshold; e.g. 0.05")
    args = parser.parse_args()

    if args.live_trades_json:
        live_trades = _load_json_list(args.live_trades_json)
    else:
        if not (args.model_id and args.from_date_jst and args.to_date_jst):
            raise ValueError("provide --live-trades-json, or --model-id with --from-date-jst/--to-date-jst")
        live_trades = _load_firestore_trades(model_id=args.model_id, mode=args.mode, from_date_jst=args.from_date_jst, to_date_jst=args.to_date_jst)
    if args.backtest_trades_json:
        backtest_trades = _load_json_list(args.backtest_trades_json)
    elif args.config and args.bars_path:
        report = run_backtest(read_bars_from_csv(args.bars_path), load_bot_config(args.config))
        backtest_trades = [trade.to_dict() for trade in report.trades]
    else:
        raise ValueError("provide --backtest-trades-json or both --config and --bars-path")

    threshold = args.slack_on_threshold if args.slack_on_threshold is not None else 0.20
    diff = compare_trade_logs(live_trades=live_trades, backtest_trades=backtest_trades, pnl_threshold=threshold)
    output_path = Path(args.output or "research/data/shadow_diff/latest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    if diff["summary"]["threshold_breached"] and args.slack_webhook_url:
        _post_slack(args.slack_webhook_url, diff)
    print(f"[research] shadow diff written: {output_path}")


if __name__ == "__main__":
    main()
