"""
緊急クローズスクリプト: スタックしたGMOトレードを手仕舞いする

Usage:
    python scripts/emergency_close_gmo.py --trade-id 2026-05-10T15:15:00Z_gmo_ema_pullback_15m_both_v0_LONG

環境変数は .env から自動読み込み。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # type: ignore[import-untyped]

load_dotenv()

from google.cloud.firestore import Client as FirestoreClient

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.adapters.persistence.firestore_repo import FirestoreRepository
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
)
from apps.gmo_bot.infra.config.firestore_config_repo import FirestoreConfigRepository


class PrintLogger:
    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[INFO] {message}", json.dumps(context, ensure_ascii=False) if context else "")

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[WARN] {message}", json.dumps(context, ensure_ascii=False) if context else "")

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        print(f"[ERROR] {message}", json.dumps(context, ensure_ascii=False) if context else "", file=sys.stderr)


class NoOpLock:
    def acquire_runner_lock(self, ttl_seconds: int) -> bool:
        return True

    def release_runner_lock(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="GMOスタックトレード緊急クローズ")
    parser.add_argument("--trade-id", required=True, help="クローズするtrade_id")
    parser.add_argument("--dry-run", action="store_true", help="Firestoreへの書き込みをスキップ")
    args = parser.parse_args()

    trade_id: str = args.trade_id
    dry_run: bool = args.dry_run

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "secrets/firebase-service-account.json")
    gmo_key = os.environ["GMO_API_KEY"]
    gmo_secret = os.environ["GMO_API_SECRET"]

    logger = PrintLogger()
    logger.info("緊急クローズ開始", {"trade_id": trade_id, "dry_run": dry_run})

    firestore = FirestoreClient.from_service_account_json(creds)
    config_repo = FirestoreConfigRepository(firestore)

    # trade_id からモデルIDを抽出 (例: 2026-05-10T15:15:00Z_gmo_ema_pullback_15m_both_v0_LONG)
    parts = trade_id.split("_", 1)
    if len(parts) < 2:
        print(f"trade_id の形式が不正: {trade_id}", file=sys.stderr)
        sys.exit(1)
    remainder = parts[1]  # gmo_ema_pullback_15m_both_v0_LONG
    # 末尾の _LONG / _SHORT を除去してモデルID取得
    model_id = remainder.rsplit("_", 1)[0]  # gmo_ema_pullback_15m_both_v0

    logger.info("モデルID推定", {"model_id": model_id})

    persistence = FirestoreRepository(firestore, config_repo, mode="LIVE", model_id=model_id, logger=logger)

    trade = persistence.get_trade(trade_id)
    if not isinstance(trade, dict):
        print(f"トレードが見つかりません: {trade_id}", file=sys.stderr)
        sys.exit(1)

    logger.info("トレードデータ取得", {
        "trade_id": trade.get("trade_id"),
        "state": trade.get("state"),
        "direction": trade.get("direction"),
        "pair": trade.get("pair"),
    })

    state = trade.get("state")
    if state != "CONFIRMED":
        print(f"トレードは既にクローズ済みです: state={state}")
        sys.exit(0)

    api_client = GmoApiClient(gmo_key, gmo_secret)
    execution = GmoMarginExecutionAdapter(client=api_client, logger=logger)

    current_price = execution.get_mark_price(trade["pair"])
    logger.info("現在価格取得", {"pair": trade["pair"], "mark_price": current_price})

    config = persistence.get_current_config()

    if dry_run:
        logger.info("DRY RUN: close_position は実行しません")
        print(f"現在価格: {current_price}")
        return

    lock = NoOpLock()
    result = close_position(
        ClosePositionDependencies(
            execution=execution,
            lock=lock,
            logger=logger,
            persistence=persistence,
        ),
        ClosePositionInput(
            config=config,
            trade=trade,
            close_reason="MANUAL",
            close_price=current_price,
        ),
    )

    logger.info("クローズ結果", {"status": result.status, "summary": result.summary, "trade_id": result.trade_id})

    if result.status in ("CLOSED", "PARTIALLY_CLOSED"):
        print(f"✓ トレードをクローズしました: {result.summary}")
    else:
        print(f"✗ クローズ失敗: {result.summary}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
