# crypto_trade_bot (Python v1)

Solana `SOL/USDC` 現物向けの自動売買Botです。  
Node.js 実装から Python に移行し、`domain / app / adapters / infra` のレイヤ構成で運用しています。

## 概要

- 複数モデルを Firestore から読み込み、`model_id` 単位で独立実行
- 売買実行は Jupiter (`quote/swap`) + Solana RPC 送信
- 永続化は Firestore、排他と冪等は Redis
- 起動時即実行 + 1分周期スケジューラ
- 15分モデルは `direction=BOTH` をサポート

## 前提

- Python 3.12+
- Docker / Docker Compose
- Firestore サービスアカウント JSON

## 環境変数

必須:

- `SOLANA_RPC_URL`
- `REDIS_URL`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `WALLET_KEY_PASSPHRASE`

任意:

- `SLACK_WEBHOOK_URL`（未設定でSlack通知無効）

補足:

- `pybot.main` は起動時に `load_dotenv(Path(".env"))` を実行します
- 本番VPSは `.env` を置かず、GitHub Actions から環境注入する運用を推奨
- ローカルのみ `.env.example` から作成可能

```bash
cp .env.example .env
```

## Firestore 構成

### 1) モデル設定

- `models/{model_id}`
  - 例: `enabled`, `mode`, `direction`, `wallet_key_path`
- `models/{model_id}/config/current`
  - 戦略・リスク・執行設定

`enabled` / `mode` / `direction` は `models/{model_id}` 側を正として読み込み、`config/current` の同名項目は上書きされます。

### 2) トレード保存（日付分割）

- LIVE: `models/{model_id}/trades/{YYYY-MM-DD}/items/{trade_id}`
- PAPER: `models/{model_id}/paper_trades/{YYYY-MM-DD}/items/{trade_id}`

状態キャッシュ:

- `models/{model_id}/state/open_trade`
- `models/{model_id}/state/recent_closed_trades`

`recent_closed_trades` は最大32件を保持します。

### 3) run 保存（日付分割）

- LIVE: `models/{model_id}/runs/{YYYY-MM-DD}/items/{run_doc_id}`
- PAPER: `models/{model_id}/paper_runs/{YYYY-MM-DD}/items/{run_doc_id}`

保存対象は以下のみ:

- 常時保存: `OPENED`, `CLOSED`, `FAILED`
- 条件付き保存: 実行系エラー理由の `SKIPPED`（slippage / liquidity / funds など）

保存された `SKIPPED` は同日・同理由で集約され、`occurrence_count` が加算されます。

※ `SKIPPED_ENTRY` は `save_run` 側は対応済みですが、現行 `run_cycle` では通常保存対象外です。

### 4) 全体停止フラグ

- `control/global.pause_all`
  - `true`: 新規エントリー停止
  - 既存OPENポジションのEXIT監視は継続

## 実行スケジュールと反映タイミング

- 起動時に1回即時実行
- その後は UTC 1分周期（`* * * * *`）
- 新規エントリー判定は「5分境界」または「OPENポジション保有中」のときのみ実行
- `pause_all=true` 時は OPENポジション保有モデルのみ実行

Firestore 変更反映:

- リアルタイム監視対象
  - `models` コレクション
  - `models/{model_id}/config/current`
  - `control/global`
- 加えて15分ごとのフォールバック再同期あり

## モデル/戦略制約

許可戦略:

- `ema_trend_pullback_v0`
- `ema_trend_pullback_15m_v0`
- `storm_short_v0`

制約:

- `ema_trend_pullback_v0`
  - `direction=LONG`
  - `signal_timeframe=2h|4h`
- `storm_short_v0`
  - `direction=SHORT`
- `ema_trend_pullback_15m_v0`
  - `signal_timeframe=15m`
  - `direction=LONG|SHORT|BOTH`（`BOTH`時は戦略診断 `entry_direction` を使用）

現行モデルID:

- `ema_pullback_2h_long_v0`
- `storm_2h_short_v0`
- `ema_pullback_15m_both_v0`

## 売買ロジック（現行）

- LONG: `BUY_SOL_WITH_USDC` で建て、`SELL_SOL_FOR_USDC` でクローズ
- SHORT: 現物在庫ベース（`SELL_SOL_FOR_USDC` で建て、`BUY_SOL_WITH_USDC` でクローズ）

エントリー:

- 利用可能残高の `99%` を使用（`ENTRY_BALANCE_USAGE_RATIO=0.99`）
- `ENTRY_RETRY_ATTEMPTS=3`
- 最終リトライ時のみ `slippage_bps +1`
- slippage / liquidity / insufficient funds は `SKIPPED` 扱い（状態は `CANCELED`）

EXIT:

- `TAKE_PROFIT`: 最大2回再試行
- `STOP_LOSS`: 最大5回再試行
- EXIT時slippageは段階的に拡大（TP上限30bps, SL上限120bps）
- TAKE_PROFIT で slippage/liquidity エラーは `SKIPPED`（ポジション維持）

RPC/HTTPタイムアウト:

- Jupiter quote/swap: 8秒
- Solana RPC: 8秒
- tx confirm timeout: 20秒

## Redis キー

- `lock:runner:{model_id}`: run_cycle排他
- `idem:entry:{model_id}:{bar_close_time_iso}`: 同一バー再エントリー防止
- `tx:inflight:{model_id}:{signature}`: 送信中TXトラッキング
- `alert:daily_summary:jst:{YYYY-MM-DD}`: 日次サマリ重複送信防止

## Slack 通知

通知形式:

- 基本日本語
- 詳細はコードブロック（```）

通知対象:

- Bot起動 / 停止
- 売買実行エラー（`FAILED` + 実行系 `SKIPPED`）
- 連続 `FAILED`（しきい値3）と復帰
- run_cycle 停滞（しきい値10分）と復帰
- 実行設定エラー（モデル設定読込失敗・wallet_key_path不足）
- 日次サマリ（JST 00:05〜00:14 に前日分を1回）

## セットアップ

### 1) Firestoreへ初期投入

```bash
python scripts/seed-firestore-config.py --mode LIVE
```

PAPER投入:

```bash
python scripts/seed-firestore-config.py --mode PAPER
```

制御ドキュメントのみ投入:

```bash
python scripts/seed-firestore-config.py --control-only
```

単一モデルを投入:

```bash
python scripts/seed-firestore-config.py \
  --config-path research/models/ema_pullback_15m_both_v0/config/current.json \
  --wallet-key-path /run/secrets/wallet.ema_pullback_15m_both_v0.enc.json
```

### 2) ウォレット鍵の暗号化

```bash
python scripts/encrypt-wallet.py \
  --input /path/to/id.json \
  --output /path/to/wallet.ema_pullback_2h_long_v0.enc.json \
  --passphrase "your-passphrase"
```

```bash
python scripts/encrypt-wallet.py \
  --base58 "PHANTOM_BASE58_PRIVATE_KEY" \
  --output /path/to/wallet.ema_pullback_2h_long_v0.enc.json \
  --passphrase "your-passphrase"
```

### 3) ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pybot.main
```

### 4) Docker実行

```bash
docker compose up -d --build
```

## デプロイ（GitHub Actions）

ワークフロー: `.github/workflows/deploy.yml`

- トリガ: `main` push / `workflow_dispatch`
- VPS上で `git pull` 後に `docker compose up -d --build`

必要な GitHub Secrets:

- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`
- `WALLET_KEY_PASSPHRASE`
- `SLACK_WEBHOOK_URL`（任意）

現在の compose 固定値:

- `SOLANA_RPC_URL=https://api.mainnet-beta.solana.com`
- `REDIS_URL=redis://redis:6379`
- `GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/firebase-service-account.json`

VPSに配置するシークレットファイル:

- `/opt/crypto_trade_bot/secrets/firebase-service-account.json`
- `/opt/crypto_trade_bot/secrets/wallet.<model_id>.enc.json`

## テスト

```bash
python -m unittest
```

## Research（分析）

分析系は `research/` に分離しています。詳細は `research/README.md` を参照してください。

例:

```bash
python -m research.scripts.fetch_ohlcv --pair SOL/USDC --timeframe 15m --years 0.5 --output research/data/raw/solusdc_15m.csv
python -m research.scripts.run_backtest --config research/models/ema_pullback_15m_both_v0/config/current.json --bars research/data/raw/solusdc_15m.csv --output research/data/processed/backtest_latest.json
```
