from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from apps.dex_bot.app.ports.logger_port import LoggerPort
from apps.dex_bot.infra.alerting.daily_trade_summary import DailyTradeSummaryReport, ModelDailyTradeSummary
from apps.gmo_bot.infra.alerting.daily_trade_summary import (
    DailyTradeSummaryReport as GmoDailyTradeSummaryReport,
    ModelDailyTradeSummary as GmoModelDailyTradeSummary,
)

_REQUEST_TIMEOUT_SECONDS = 5
_SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_SLACK_GET_UPLOAD_URL_EXTERNAL_URL = "https://slack.com/api/files.getUploadURLExternal"
_SLACK_COMPLETE_UPLOAD_EXTERNAL_URL = "https://slack.com/api/files.completeUploadExternal"
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
    bot_token: str | None = None
    daily_summary_channel_id: str | None = None
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
    def __init__(
        self,
        *,
        config: SlackAlertConfig,
        logger: LoggerPort,
        dedupe_store: Any | None = None,
        dedupe_namespace: str = "bot",
    ):
        webhook_url = (config.webhook_url or "").strip()
        self._webhook_url = webhook_url if webhook_url else None
        bot_token = (config.bot_token or "").strip()
        self._bot_token = bot_token if bot_token else None
        daily_summary_channel_id = (config.daily_summary_channel_id or "").strip()
        self._daily_summary_channel_id = daily_summary_channel_id if daily_summary_channel_id else None
        self._logger = logger
        self._duplicate_suppression_seconds = max(config.duplicate_suppression_seconds, 0)
        self._last_sent_by_key: dict[str, datetime] = {}
        self._dedupe_store = dedupe_store
        normalized_namespace = dedupe_namespace.strip()
        self._dedupe_namespace = normalized_namespace or "bot"

    @property
    def enabled(self) -> bool:
        return self._webhook_url is not None or (self._bot_token is not None and self._daily_summary_channel_id is not None)

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

    def notify_trade_closed(
        self,
        *,
        model_id: str,
        trade_id: str,
        pair: str,
        direction: str,
        close_reason: str,
        entry_price: float | None,
        exit_price: float | None,
        gross_pnl: float | None,
        fee: float | None,
        net_pnl: float | None,
        quote_ccy: str,
        cumulative_gross_pnl: float | None = None,
        cumulative_net_pnl: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"model={model_id}",
            f"trade_id={trade_id}",
            f"pair={pair}",
            f"direction={direction}",
            f"reason={close_reason}",
        ]
        if entry_price is not None:
            lines.append(f"entry_price={entry_price:.6f}")
        if exit_price is not None:
            lines.append(f"exit_price={exit_price:.6f}")
        if gross_pnl is not None:
            lines.append(
                f"gross_pnl_{quote_ccy.lower()}={self._format_quote_value(gross_pnl, quote_ccy)}"
            )
        if fee is not None:
            lines.append(f"fee_{quote_ccy.lower()}={self._format_quote_value(fee, quote_ccy)}")
        if net_pnl is not None:
            lines.append(f"net_pnl_{quote_ccy.lower()}={self._format_quote_value(net_pnl, quote_ccy)}")
        message = self._format_message(self._trade_close_title(close_reason), lines)
        self._send(message=message, dedupe_key=f"trade_closed:{trade_id}:{close_reason}")

    def notify_runtime_config_error(
        self,
        *,
        model_id: str,
        error: str,
        context: str = "failed_to_load_model_config",
    ) -> None:
        if not self.enabled:
            return
        lines = [
            f"model={model_id}",
            f"context={context}",
            f"error={error}",
        ]
        message = self._format_message(
            "実行設定エラー",
            lines,
        )
        dedupe_key = f"runtime_config_error:{model_id}:{context}:{error}"
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
        message = f"{header}\n{generated}\n```\n" + "\n".join(self._build_dex_daily_summary_lines(report)) + "\n```"
        self._send(message=message, dedupe_key=f"daily_summary_jst:{report.target_date_jst}")

    def notify_combined_daily_trade_summary_jst(
        self,
        *,
        dex_report: DailyTradeSummaryReport,
        gmo_report: GmoDailyTradeSummaryReport,
    ) -> None:
        if not self.enabled:
            return

        target_date_jst = dex_report.target_date_jst
        message = self._build_combined_daily_summary_message(dex_report=dex_report, gmo_report=gmo_report)
        self._send(message=message, dedupe_key=f"daily_summary_jst:{target_date_jst}")

    def notify_combined_daily_trade_summary_with_charts_jst(
        self,
        *,
        dex_report: DailyTradeSummaryReport,
        gmo_report: GmoDailyTradeSummaryReport,
        dex_chart_png: bytes | None,
        gmo_chart_png: bytes | None,
    ) -> None:
        if not self.enabled:
            return

        target_date_jst = dex_report.target_date_jst
        dedupe_key = f"daily_summary_jst:{target_date_jst}"
        message = self._build_combined_daily_summary_message(dex_report=dex_report, gmo_report=gmo_report)
        if self._bot_token is None or self._daily_summary_channel_id is None:
            self._send(message=message, dedupe_key=dedupe_key)
            return
        if not self._should_send(dedupe_key):
            return

        try:
            thread_ts = self._post_daily_summary_message_with_bot(message=message)
        except Exception as error:
            self._logger.warn(
                "failed to post slack daily summary with bot token; falling back to webhook",
                {"error": str(error), "dedupe_key": dedupe_key},
            )
            self._post_webhook(message=message, dedupe_key=None)
            return

        chart_payloads = [
            ("dex_balance_trend.png", "DEX balance trend", dex_chart_png),
            ("gmo_balance_trend.png", "GMO balance trend", gmo_chart_png),
        ]
        for filename, title, chart_png in chart_payloads:
            if chart_png is None:
                continue
            try:
                self._upload_chart_png_with_bot(
                    content=chart_png,
                    filename=filename,
                    title=title,
                    thread_ts=thread_ts,
                )
            except Exception as error:
                self._logger.warn(
                    "failed to upload slack daily summary chart",
                    {"error": str(error), "filename": filename, "thread_ts": thread_ts},
                )

    def _build_combined_daily_summary_message(
        self,
        *,
        dex_report: DailyTradeSummaryReport,
        gmo_report: GmoDailyTradeSummaryReport,
    ) -> str:
        target_date_jst = dex_report.target_date_jst
        generated_at_jst = max(dex_report.generated_at_jst, gmo_report.generated_at_jst)
        header = f"*【日次トレード結果サマリ（JST）】* `{target_date_jst}`"
        generated = f"集計時刻: `{generated_at_jst}`"
        sections = [
            "*DEX*",
            "```",
            *self._build_dex_daily_summary_lines(dex_report),
            "```",
            "*GMO*",
            "```",
            *self._build_gmo_daily_summary_lines(gmo_report),
            "```",
        ]
        return f"{header}\n{generated}\n" + "\n".join(sections)

    def _send(self, *, message: str, dedupe_key: str | None) -> None:
        if dedupe_key and not self._should_send(dedupe_key):
            return
        self._post_webhook(message=message, dedupe_key=dedupe_key)

    def _post_webhook(self, *, message: str, dedupe_key: str | None) -> None:
        if self._webhook_url is None:
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

    def _post_daily_summary_message_with_bot(self, *, message: str) -> str:
        payload = self._post_slack_api_json(
            _SLACK_CHAT_POST_MESSAGE_URL,
            {
                "channel": self._daily_summary_channel_id,
                "text": message,
                "mrkdwn": True,
            },
        )
        thread_ts = payload.get("ts")
        if not isinstance(thread_ts, str) or thread_ts.strip() == "":
            raise RuntimeError("Slack chat.postMessage response is missing ts")
        return thread_ts

    def _upload_chart_png_with_bot(
        self,
        *,
        content: bytes,
        filename: str,
        title: str,
        thread_ts: str,
    ) -> None:
        # This is the raw Web API flow wrapped by Slack SDK's files_upload_v2 helper.
        upload_payload = self._post_slack_api_json(
            _SLACK_GET_UPLOAD_URL_EXTERNAL_URL,
            {
                "filename": filename,
                "length": len(content),
            },
        )
        upload_url = upload_payload.get("upload_url")
        file_id = upload_payload.get("file_id")
        if not isinstance(upload_url, str) or not isinstance(file_id, str):
            raise RuntimeError("Slack upload URL response is missing upload_url or file_id")

        upload_response = requests.post(
            upload_url,
            files={"file": (filename, content, "image/png")},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        upload_response.raise_for_status()

        self._post_slack_api_json(
            _SLACK_COMPLETE_UPLOAD_EXTERNAL_URL,
            {
                "files": [{"id": file_id, "title": title}],
                "channel_id": self._daily_summary_channel_id,
                "thread_ts": thread_ts,
            },
        )

    def _post_slack_api_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._bot_token is None:
            raise RuntimeError("Slack bot token is not configured")
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Slack API response is invalid: {url}")
        if data.get("ok") is not True:
            raise RuntimeError(f"Slack API call failed: url={url} error={data.get('error')}")
        return data

    def _should_send(self, dedupe_key: str) -> bool:
        if self._duplicate_suppression_seconds <= 0:
            return True
        now = datetime.now(tz=UTC)
        shared_dedupe_key = f"slack_dedupe:{self._dedupe_namespace}:{dedupe_key}"
        if self._dedupe_store is not None:
            try:
                claimed = self._dedupe_store.set(
                    shared_dedupe_key,
                    now.isoformat(),
                    ex=self._duplicate_suppression_seconds,
                    nx=True,
                )
            except Exception as error:
                self._logger.warn(
                    "shared slack dedupe failed",
                    {"error": str(error), "dedupe_key": dedupe_key, "namespace": self._dedupe_namespace},
                )
            else:
                if claimed:
                    self._last_sent_by_key[dedupe_key] = now
                    return True
                return False
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

    def _trade_close_title(self, close_reason: str) -> str:
        if close_reason == "TAKE_PROFIT":
            return "利確確定"
        if close_reason == "STOP_LOSS":
            return "損切確定"
        return "Trade決済確定"

    def _format_quote_value(self, value: float, quote_ccy: str) -> str:
        if quote_ccy == "JPY":
            return f"{value:.2f}"
        return f"{value:.4f}"

    def _build_dex_daily_summary_lines(self, report: DailyTradeSummaryReport) -> list[str]:
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
        return lines

    def _build_gmo_daily_summary_lines(self, report: GmoDailyTradeSummaryReport) -> list[str]:
        separator = "-" * 138
        lines = [
            f"{'model_id':<{_MODEL_ID_COLUMN_WIDTH}}"
            "  closed  win  loss  win_rate    pnl_jpy    fee_jpy  avg_slip  fail_run  skip_run fail_trd cancel_trd",
            separator,
        ]
        if report.model_summaries:
            for summary in report.model_summaries:
                lines.append(self._format_gmo_daily_summary_row(summary))
        else:
            lines.append("(対象モデルなし)")
        lines.append(separator)
        lines.append(
            self._format_gmo_daily_total_row(
                closed=report.total_closed_trades,
                win=report.total_win_trades,
                loss=report.total_loss_trades,
                win_rate=report.total_win_rate_pct,
                pnl_jpy=report.total_realized_pnl_jpy,
                fee_jpy=report.total_estimated_fees_jpy,
                avg_slip_bps=report.total_avg_slippage_bps,
                failed_runs=report.total_failed_runs,
                skipped_runs=report.total_skipped_runs,
                failed_trades=report.total_failed_trades,
                canceled_trades=report.total_canceled_trades,
            )
        )
        return lines

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

    def _format_gmo_daily_summary_row(self, summary: GmoModelDailyTradeSummary) -> str:
        model_id = summary.model_id[:_MODEL_ID_COLUMN_WIDTH]
        return (
            f"{model_id:<{_MODEL_ID_COLUMN_WIDTH}}"
            f"{summary.closed_trades:>8}"
            f"{summary.win_trades:>5}"
            f"{summary.loss_trades:>6}"
            f"{summary.win_rate_pct:>10.1f}%"
            f"{summary.realized_pnl_jpy:>11.0f}"
            f"{summary.estimated_fees_jpy:>11.0f}"
            f"{summary.avg_slippage_bps:>10.2f}"
            f"{summary.failed_runs:>10}"
            f"{summary.skipped_runs:>10}"
            f"{summary.failed_trades:>9}"
            f"{summary.canceled_trades:>11}"
        )

    def _format_gmo_daily_total_row(
        self,
        *,
        closed: int,
        win: int,
        loss: int,
        win_rate: float,
        pnl_jpy: float,
        fee_jpy: float,
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
            f"{pnl_jpy:>11.0f}"
            f"{fee_jpy:>11.0f}"
            f"{avg_slip_bps:>10.2f}"
            f"{failed_runs:>10}"
            f"{skipped_runs:>10}"
            f"{failed_trades:>9}"
            f"{canceled_trades:>11}"
        )
