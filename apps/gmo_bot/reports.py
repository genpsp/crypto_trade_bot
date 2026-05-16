"""CLI entry point for the gmo_bot performance analysis report.

Usage:
    python -m apps.gmo_bot.reports \\
        --model-id gmo_ema_pullback_15m_both_v0 \\
        --from 2026-04-01 --to 2026-05-15 \\
        --mode live \\
        --output ./reports/2026-05-15_gmo_ema_pullback_15m.html \\
        [--slack]
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv
from google.cloud.firestore import Client as FirestoreClient

from apps.gmo_bot.adapters.persistence.firestore_repo import FirestoreRepository
from apps.gmo_bot.app.reporting.generate_report import GenerateReportRequest, generate_report
from apps.gmo_bot.infra.alerting import SlackAlertConfig, SlackNotifier
from apps.gmo_bot.infra.config.env import load_env
from apps.gmo_bot.infra.config.firestore_config_repo import FirestoreConfigRepository
from apps.gmo_bot.infra.logging.logger import create_logger

JST = timezone(timedelta(hours=9))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a gmo_bot performance analysis report")
    parser.add_argument("--model-id", required=True, help="Target model_id")
    parser.add_argument("--from", dest="from_date", required=True, help="Period start (YYYY-MM-DD, JST)")
    parser.add_argument("--to", dest="to_date", required=True, help="Period end (YYYY-MM-DD, JST, inclusive)")
    parser.add_argument("--mode", choices=["live", "paper"], default="live", help="Collection mode (default: live)")
    parser.add_argument("--output", help="Output HTML path (default: ./reports/{to}_{model_id}.html)")
    parser.add_argument("--slack", action="store_true", help="Post a headline summary to Slack")
    return parser.parse_args(argv)


def _validate_date(value: str, *, label: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise SystemExit(f"--{label} must be YYYY-MM-DD, got: {value}") from error
    return value


def _resolve_output(args: argparse.Namespace) -> Path:
    if args.output:
        return Path(args.output)
    safe_model_id = args.model_id.replace("/", "_")
    return Path("reports") / f"{args.to_date}_{safe_model_id}.html"


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=Path(".env"))
    logger = create_logger("gmo-bot-report")
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    from_date = _validate_date(args.from_date, label="from")
    to_date = _validate_date(args.to_date, label="to")
    mode = "PAPER" if args.mode == "paper" else "LIVE"

    env = load_env()
    firestore = FirestoreClient.from_service_account_json(env.GOOGLE_APPLICATION_CREDENTIALS)
    config_repo = FirestoreConfigRepository(firestore)
    repo = FirestoreRepository(
        firestore=firestore,
        config_repo=config_repo,
        mode=mode,
        model_id=args.model_id,
        logger=logger,
    )

    request = GenerateReportRequest(
        model_id=args.model_id,
        mode=mode,
        from_date_jst=from_date,
        to_date_jst=to_date,
    )

    logger.info(
        "generating performance report",
        {"model_id": args.model_id, "from": from_date, "to": to_date, "mode": mode},
    )

    result = generate_report(repo, request)

    output_path = _resolve_output(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.html, encoding="utf-8")

    logger.info(
        "report generated",
        {"output": str(output_path), "size_bytes": output_path.stat().st_size, "headline": result.headline},
    )
    print(result.headline)
    print(f"saved: {output_path}")

    if args.slack:
        webhook_url = env.SLACK_WEBHOOK_URL
        if not webhook_url:
            logger.warn("SLACK_WEBHOOK_URL not set; skipping slack notification", {})
        else:
            notifier = SlackNotifier(
                config=SlackAlertConfig(webhook_url=webhook_url, duplicate_suppression_seconds=0),
                logger=logger,
            )
            message = (
                "Performance report generated\n"
                f"{result.headline}\n"
                f"file: {output_path.resolve()}"
            )
            # `_send` is intentionally used: SlackNotifier exposes only domain-specific
            # notify_* helpers, and we want a plain headline post here.
            notifier._send(message=message, dedupe_key=None)  # noqa: SLF001

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logger = create_logger("gmo-bot-report")
        logger.error("report generation failed", {"error": str(error)})
        raise
