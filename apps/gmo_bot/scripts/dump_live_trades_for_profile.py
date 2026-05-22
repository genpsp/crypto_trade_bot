"""Dump LIVE / PAPER trade records from Firestore into a JSON file that
``research/scripts/build_execution_profile.py`` can consume.

The build_execution_profile script already has a direct Firestore reader, but
exporting the raw records to disk gives us:

* a hand-curatable artefact for inspection / spot-fixing bad rows
* a deterministic input for ``StochasticExecutionModel`` profile rebuilds
* a portable corpus that can be checked into ``research/data/execution_profiles/raw_trades/``

Usage:

    python -m apps.gmo_bot.scripts.dump_live_trades_for_profile \\
        --model-id gmo_ema_pullback_15m_both_v0 \\
        --mode LIVE \\
        --from-date-jst 2025-09-01 \\
        --to-date-jst 2026-05-15 \\
        --output research/data/execution_profiles/raw_trades/gmo_soljpy_live_2025-09_2026-05.json

Then build the profile:

    python -m research.scripts.build_execution_profile \\
        --broker GMO_COIN \\
        --pair SOL/JPY \\
        --input research/data/execution_profiles/raw_trades/gmo_soljpy_live_2025-09_2026-05.json \\
        --output research/data/execution_profiles/gmo_soljpy.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_trades_from_firestore(
    *,
    model_id: str,
    mode: str,
    from_date_jst: str,
    to_date_jst: str,
) -> list[dict[str, Any]]:
    try:
        from google.cloud import firestore
    except ImportError as error:  # pragma: no cover - optional integration path
        raise RuntimeError(
            "google-cloud-firestore is required to read LIVE trades; "
            "install it with `pip install google-cloud-firestore` and authenticate "
            "via Application Default Credentials."
        ) from error

    client = firestore.Client()
    collection_name = "paper_trades" if mode.upper() == "PAPER" else "trades"
    from_date = datetime.fromisoformat(from_date_jst).date()
    to_date = datetime.fromisoformat(to_date_jst).date()

    trades: list[dict[str, Any]] = []
    cursor = from_date
    while cursor <= to_date:
        day = cursor.isoformat()
        items = (
            client.collection("models")
            .document(model_id)
            .collection(collection_name)
            .document(day)
            .collection("items")
        )
        for doc in items.stream():
            payload = doc.to_dict()
            if isinstance(payload, dict):
                payload.setdefault("trade_date", day)
                payload.setdefault("model_id", model_id)
                payload.setdefault("mode", mode.upper())
                trades.append(payload)
        cursor = datetime.fromordinal(cursor.toordinal() + 1).date()
    return trades


def _coerce_serializable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _coerce_serializable(sub_value) for key, sub_value in value.items()}
    if isinstance(value, list):
        return [_coerce_serializable(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", required=True, help="Firestore model_id (e.g. gmo_ema_pullback_15m_both_v0)")
    parser.add_argument("--mode", required=True, choices=["LIVE", "PAPER"])
    parser.add_argument("--from-date-jst", required=True)
    parser.add_argument("--to-date-jst", required=True)
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    trades = _load_trades_from_firestore(
        model_id=args.model_id,
        mode=args.mode,
        from_date_jst=args.from_date_jst,
        to_date_jst=args.to_date_jst,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([_coerce_serializable(trade) for trade in trades], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[gmo_bot] dumped {len(trades)} trades → {output}")


if __name__ == "__main__":
    main()
