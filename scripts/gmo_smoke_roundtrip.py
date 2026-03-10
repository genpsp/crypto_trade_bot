from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.app.ports.execution_port import SubmitCloseOrderRequest, SubmitEntryOrderRequest
from apps.gmo_bot.app.usecases.manual_smoke_roundtrip import SmokeRoundtripPlan, build_smoke_roundtrip_plan
from apps.gmo_bot.infra.config.env import load_env
from apps.gmo_bot.infra.logging.logger import create_logger

DEFAULT_SLIPPAGE_BPS = 10
DEFAULT_CONFIRM_TIMEOUT_MS = 20_000
DEFAULT_MAX_NOTIONAL_JPY = 2_000.0
DEFAULT_SLEEP_BEFORE_CLOSE_MS = 1_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit one tiny GMO live entry order and immediately close it.",
    )
    parser.add_argument("--direction", choices=("LONG", "SHORT"), default="LONG")
    parser.add_argument("--size-sol", type=float, default=None, help="Optional SOL size. Defaults to GMO min_order_size.")
    parser.add_argument("--max-notional-jpy", type=float, default=DEFAULT_MAX_NOTIONAL_JPY)
    parser.add_argument("--slippage-bps", type=int, default=DEFAULT_SLIPPAGE_BPS)
    parser.add_argument("--confirm-timeout-ms", type=int, default=DEFAULT_CONFIRM_TIMEOUT_MS)
    parser.add_argument("--sleep-before-close-ms", type=int, default=DEFAULT_SLEEP_BEFORE_CLOSE_MS)
    parser.add_argument("--execute", action="store_true", help="Actually place live orders. Without this flag, plan only.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return parser.parse_args()


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _build_runtime_context(args: argparse.Namespace) -> tuple[GmoMarginExecutionAdapter, SmokeRoundtripPlan, float]:
    load_dotenv(REPO_ROOT / ".env")
    env = load_env()
    logger = create_logger("gmo-smoke")
    client = GmoApiClient(api_key=env.GMO_API_KEY, api_secret=env.GMO_API_SECRET)
    execution = GmoMarginExecutionAdapter(client, logger)
    mark_price = execution.get_mark_price("SOL/JPY")
    available_margin_jpy = execution.get_available_margin_jpy()
    symbol_rule = execution.get_symbol_rule("SOL/JPY")
    plan = build_smoke_roundtrip_plan(
        direction=args.direction,
        mark_price=mark_price,
        symbol_rule=symbol_rule,
        requested_size_sol=args.size_sol,
        max_notional_jpy=args.max_notional_jpy,
    )
    if available_margin_jpy < plan.estimated_notional_jpy:
        raise RuntimeError(
            f"available margin {available_margin_jpy:.2f} JPY is below estimated notional {plan.estimated_notional_jpy:.2f} JPY"
        )
    return execution, plan, available_margin_jpy


def _execute_roundtrip(
    *,
    execution: GmoMarginExecutionAdapter,
    plan: SmokeRoundtripPlan,
    slippage_bps: int,
    confirm_timeout_ms: int,
    sleep_before_close_ms: int,
) -> dict[str, Any]:
    entry_submission = execution.submit_entry_order(
        SubmitEntryOrderRequest(
            side=plan.entry_side,
            size_sol=plan.size_sol,
            slippage_bps=slippage_bps,
            reference_price=plan.reference_price,
        )
    )
    entry_confirmation = execution.confirm_order(entry_submission.order_id, confirm_timeout_ms)
    if not entry_confirmation.confirmed or entry_confirmation.result is None:
        raise RuntimeError(
            f"entry order did not confirm: order_id={entry_submission.order_id}, error={entry_confirmation.error or 'unknown'}"
        )

    entry_result = entry_confirmation.result
    lots = list(entry_result.get("lots") or [])
    if not lots:
        raise RuntimeError(f"entry confirmed but no lots returned: order_id={entry_submission.order_id}")

    if sleep_before_close_ms > 0:
        time.sleep(sleep_before_close_ms / 1000)

    close_reference_price = float(entry_result["avg_fill_price"])
    close_submission = execution.submit_close_order(
        SubmitCloseOrderRequest(
            side=plan.close_side,
            lots=lots,
            slippage_bps=slippage_bps,
            reference_price=close_reference_price,
        )
    )
    close_confirmation = execution.confirm_order(close_submission.order_id, confirm_timeout_ms)
    if not close_confirmation.confirmed or close_confirmation.result is None:
        raise RuntimeError(
            f"close order did not confirm: order_id={close_submission.order_id}, error={close_confirmation.error or 'unknown'}"
        )

    close_result = close_confirmation.result
    close_avg_fill_price = float(close_result["avg_fill_price"])
    entry_avg_fill_price = float(entry_result["avg_fill_price"])
    realized_pnl_jpy = round(
        (close_avg_fill_price - entry_avg_fill_price) * plan.size_sol if plan.direction == "LONG" else
        (entry_avg_fill_price - close_avg_fill_price) * plan.size_sol,
        6,
    )
    return {
        "entry_order_id": entry_submission.order_id,
        "entry_result": entry_result,
        "close_order_id": close_submission.order_id,
        "close_result": close_result,
        "realized_pnl_jpy_before_fees": realized_pnl_jpy,
    }


def main() -> int:
    args = parse_args()
    execution, plan, available_margin_jpy = _build_runtime_context(args)
    payload = {
        "mode": "plan" if not args.execute else "execute",
        "plan": asdict(plan),
        "available_margin_jpy": round(available_margin_jpy, 2),
        "slippage_bps": args.slippage_bps,
        "confirm_timeout_ms": args.confirm_timeout_ms,
        "sleep_before_close_ms": args.sleep_before_close_ms,
    }
    _print_payload(payload, as_json=args.json)

    if not args.execute:
        print("No live order placed. Re-run with --execute to submit the roundtrip.")
        return 0

    result = _execute_roundtrip(
        execution=execution,
        plan=plan,
        slippage_bps=args.slippage_bps,
        confirm_timeout_ms=args.confirm_timeout_ms,
        sleep_before_close_ms=args.sleep_before_close_ms,
    )
    _print_payload({"mode": "result", **result}, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
