from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from pybot.app.ports.logger_port import LoggerPort
from pybot.infra.alerting.daily_trade_summary import DailyTradeSummaryReport, ModelDailyTradeSummary

_REQUEST_TIMEOUT_SECONDS = 5
_DEFAULT_DUPLICATE_SUPPRESSION_SECONDS = 300
_MODEL_ID_COLUMN_WIDTH = 28
_EXECUTION_ERROR_SKIP_MARKERS = (
    "insufficient funds",
    "slippage exceeded",
    "route/liquidity unavailable",
    "entry execution skipped",
    "exit slippage exceeded",
    "exit route/liquidity unavailable",
)


@dataclass(frozen=True)
class SlackAlertConfig:
    webhook_url: str | None
    consecutive_failure_threshold: int = 3
    stale_minutes: int = 10
    duplicate_suppression_seconds: int = _DEFAULT_DUPLICATE_SUPPRESSION_SECONDS


def is_execution_error_result(result: str | None, summary: str | None) -> bool:
    if result == "FAILED":
        return True
    if result != "SKIPPED":
        return False
    normalized = (summary or "").strip().lower()
    if not normalized.startswith("skipped:"):
        return False
    return any(marker in normalized for marker in _EXECUTION_ERROR_SKIP_MARKERS)


class SlackNotifier:
    def __init__(self, *, config: SlackAlertConfig, logger: LoggerPort):
        webhook_url = (config.webhook_url or "").strip()
        self._webhook_url = webhook_url if webhook_url else None
        self._logger = logger
        self._duplicate_suppression_seconds = max(config.duplicate_suppression_seconds, 0)
        self._last_sent_by_key: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return self._webhook_url is not None

    def notify_startup(self, models: list[dict[str, str]]) -> None:
        if not self.enabled:
            return
        model_lines = [
            (
                f"{model.get('model_id', '?')} "
                f"(mode={model.get('mode', '?')}, strategy={model.get('strategy', '?')})"
            )
            for model in models
        ]
        if not model_lines:
            model_lines = ["(有効モデルなし)"]
        message = self._format_message(
            "Bot起動",
            model_lines,
        )
        self._send(message=message, dedupe_key="startup")

    def notify_shutdown(self, *, reason: str) -> None:
        if not self.enabled:
            return
        message = self._format_message(
            "Bot停止",
            [f"reason={reason}"],
        )
        self._send(message=message, dedupe_key="shutdown")

    def notify_trade_error(
        self,
        *,
        model_id: str,
        result: str,
        summary: str,
        run_id: str | None,
        trade_id: str | None,
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"model={model_id}",
            f"result={result}",
            f"summary={summary}",
        ]
        if run_id:
            lines.append(f"run_id={run_id}")
        if trade_id:
            lines.append(f"trade_id={trade_id}")
        message = self._format_message(
            "売買実行エラー",
            lines,
        )
        dedupe_key = f"trade_error:{model_id}:{result}:{summary}"
        self._send(message=message, dedupe_key=dedupe_key)

    def notify_consecutive_failures(
        self,
        *,
        model_id: str,
        streak: int,
        threshold: int,
        run_id: str | None,
        summary: str,
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"model={model_id}",
            f"streak={streak}",
            f"threshold={threshold}",
            f"summary={summary}",
        ]
        if run_id:
            lines.append(f"run_id={run_id}")
        message = self._format_message(
            "連続失敗を検知",
            lines,
        )
        dedupe_key = f"consecutive_failures:{model_id}:{streak}"
        self._send(message=message, dedupe_key=dedupe_key)

    def notify_failure_streak_recovered(
        self,
        *,
        model_id: str,
        previous_streak: int,
        latest_result: str,
        summary: str,
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"model={model_id}",
            f"previous_streak={previous_streak}",
            f"latest_result={latest_result}",
            f"summary={summary}",
        ]
        message = self._format_message(
            "連続失敗から復帰",
            lines,
        )
        self._send(message=message, dedupe_key=f"failure_recovered:{model_id}")

    def notify_stale_cycle(
        self,
        *,
        elapsed_seconds: int,
        threshold_minutes: int,
        model_ids: list[str],
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"elapsed_seconds={elapsed_seconds}",
            f"threshold_minutes={threshold_minutes}",
            f"models={','.join(model_ids) if model_ids else '(none)'}",
        ]
        message = self._format_message(
            "run_cycle停滞を検知",
            lines,
        )
        self._send(message=message, dedupe_key="stale_cycle")

    def notify_stale_cycle_recovered(self, *, model_ids: list[str]) -> None:
        if not self.enabled:
            return
        message = self._format_message(
            "run_cycle停滞から復帰",
            [f"models={','.join(model_ids) if model_ids else '(none)'}"],
        )
        self._send(message=message, dedupe_key="stale_cycle_recovered")

    def notify_daily_trade_summary_jst(self, *, report: DailyTradeSummaryReport) -> None:
        if not self.enabled:
            return

        header = f"*【日次トレード結果サマリ（JST）】* `{report.target_date_jst}`"
        generated = f"集計時刻: `{report.generated_at_jst}`"
        separator = "-" * 138

        lines = [
            f"{'model_id':<{_MODEL_ID_COLUMN_WIDTH}}"
            "  closed  win  loss  win_rate   pnl_usdc   fee_usdc  avg_slip  fail_run  skip_run fail_trd cancel_trd",
            separator,
        ]
        if report.model_summaries:
            for summary in report.model_summaries:
                lines.append(self._format_daily_summary_row(summary))
        else:
            lines.append("(対象モデルなし)")

        lines.append(separator)
        lines.append(
            self._format_daily_total_row(
                closed=report.total_closed_trades,
                win=report.total_win_trades,
                loss=report.total_loss_trades,
                win_rate=report.total_win_rate_pct,
                pnl_usdc=report.total_realized_pnl_usdc,
                fee_usdc=report.total_estimated_fees_usdc,
                avg_slip_bps=report.total_avg_slippage_bps,
                failed_runs=report.total_failed_runs,
                skipped_runs=report.total_skipped_runs,
                failed_trades=report.total_failed_trades,
                canceled_trades=report.total_canceled_trades,
            )
        )

        message = f"{header}\n{generated}\n```\n" + "\n".join(lines) + "\n```"
        self._send(message=message, dedupe_key=f"daily_summary_jst:{report.target_date_jst}")

    def _send(self, *, message: str, dedupe_key: str | None) -> None:
        if self._webhook_url is None:
            return
        if dedupe_key and not self._should_send(dedupe_key):
            return
        try:
            response = requests.post(
                self._webhook_url,
                json={"text": message},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as error:
            self._logger.warn(
                "failed to post slack alert",
                {"error": str(error), "dedupe_key": dedupe_key, "message": message},
            )

    def _should_send(self, dedupe_key: str) -> bool:
        if self._duplicate_suppression_seconds <= 0:
            return True
        now = datetime.now(tz=UTC)
        last_sent_at = self._last_sent_by_key.get(dedupe_key)
        if last_sent_at is not None:
            elapsed = (now - last_sent_at).total_seconds()
            if elapsed < self._duplicate_suppression_seconds:
                return False
        self._last_sent_by_key[dedupe_key] = now
        return True

    def _format_message(self, title: str, lines: list[str]) -> str:
        detail = "\n".join(lines)
        return f"{title}\n```\n{detail}\n```"

    def _format_daily_summary_row(self, summary: ModelDailyTradeSummary) -> str:
        model_id = summary.model_id[:_MODEL_ID_COLUMN_WIDTH]
        return (
            f"{model_id:<{_MODEL_ID_COLUMN_WIDTH}}"
            f"{summary.closed_trades:>8}"
            f"{summary.win_trades:>5}"
            f"{summary.loss_trades:>6}"
            f"{summary.win_rate_pct:>10.1f}%"
            f"{summary.realized_pnl_usdc:>11.2f}"
            f"{summary.estimated_fees_usdc:>11.3f}"
            f"{summary.avg_slippage_bps:>10.2f}"
            f"{summary.failed_runs:>10}"
            f"{summary.skipped_runs:>10}"
            f"{summary.failed_trades:>9}"
            f"{summary.canceled_trades:>11}"
        )

    def _format_daily_total_row(
        self,
        *,
        closed: int,
        win: int,
        loss: int,
        win_rate: float,
        pnl_usdc: float,
        fee_usdc: float,
        avg_slip_bps: float,
        failed_runs: int,
        skipped_runs: int,
        failed_trades: int,
        canceled_trades: int,
    ) -> str:
        return (
            f"{'TOTAL':<{_MODEL_ID_COLUMN_WIDTH}}"
            f"{closed:>8}"
            f"{win:>5}"
            f"{loss:>6}"
            f"{win_rate:>10.1f}%"
            f"{pnl_usdc:>11.2f}"
            f"{fee_usdc:>11.3f}"
            f"{avg_slip_bps:>10.2f}"
            f"{failed_runs:>10}"
            f"{skipped_runs:>10}"
            f"{failed_trades:>9}"
            f"{canceled_trades:>11}"
        )
