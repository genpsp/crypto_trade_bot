# crypto_trade_bot (Python v1)

Node.js 実装を Python に全面移行した Solana 現物自動売買Bot です。  
レイヤ分離は `domain / app / adapters / infra` のまま維持しています。

- エントリー: 設定タイムフレーム（`15m` / `2h` / `4h`）のクローズ時
- 複数モデル対応: `models/{model_id}/config/current`
- ロングモデル + Stormショートモデル（ショートは初期無効）
- 損切り: スイング安値 + `max_loss_per_trade_pct` で締める
- 利確: `R` 倍（`take_profit_r_multiple`）
- 実行: Jupiter quote/swap + Solana署名送信
- 永続化: Firestore（`models/{model_id}/...`）
- 重複防止: Redis（`lock:runner:{model_id}`, `idem:entry:{model_id}:*`, `tx:inflight:{model_id}:*`）

## 1. 前提

- Python 3.12+
- pip
- Docker / Docker Compose
- Firestore サービスアカウントJSON

## 2. 環境変数（必須4 + 任意1）

`.env.example` を `.env` にコピーして設定:

- `SOLANA_RPC_URL`
- `REDIS_URL`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `WALLET_KEY_PASSPHRASE`
- `SLACK_WEBHOOK_URL`（任意: 未設定なら通知無効）

`GOOGLE_APPLICATION_CREDENTIALS` は相対/絶対パスどちらでも可。  
相対パスは `docker-compose.yml` があるプロジェクトルート基準です。

Slack通知の対象:
- 売買エラー（`FAILED` と、実行系 `SKIPPED`）
- 同一モデルの連続 `FAILED`（デフォルト3回）
- run_cycle 停滞（デフォルト10分）
- 起動 / 停止
- 日次トレード結果サマリ（JST 00:05 に前日分を送信）

## 3. Firestore 事前準備

### 3.1 コレクション

- `models/{model_id}/config/current`
- `control/global`（全体制御）
- `models/{model_id}/trades`（LIVE）
- `models/{model_id}/runs`（LIVE）
- `models/{model_id}/paper_trades`（PAPER）
- `models/{model_id}/paper_runs`（PAPER）

`runs` / `paper_runs` は日付で分割して保存します

- `models/{model_id}/runs/{YYYY-MM-DD}/items/{run_doc_id}`（LIVE）
- `models/{model_id}/paper_runs/{YYYY-MM-DD}/items/{run_doc_id}`（PAPER）

同日・同理由の `SKIPPED` / `SKIPPED_ENTRY` は新規作成せず、同じ `run_doc_id` を更新して `occurrence_count` を加算します。

`control/global` に次のフラグを置くと、全モデルの `run_cycle` を一時停止できます。

- `pause_all: true` で新規エントリーを全停止（OPENポジションのEXIT監視は継続）
- `pause_all: false` で通常運転に戻す

### 3.2 config 投入

```bash
python scripts/seed-firestore-config.py --mode PAPER
```

LIVE投入:

```bash
python scripts/seed-firestore-config.py --mode LIVE
```

LIVEモデルは `models/{model_id}.wallet_key_path` が必須です。  
例:

```bash
python scripts/seed-firestore-config.py \
  --config-path research/models/ema_pullback_15m_both_v0/config/current.json \
  --wallet-key-path /run/secrets/wallet.ema_pullback_15m_both_v0.enc.json
```

## 4. Wallet 準備（Phantom連携）

`id.json` または Phantom base58 秘密鍵を暗号化:

```bash
python scripts/encrypt-wallet.py --input /path/to/id.json --output /path/to/wallet.ema_pullback_2h_long_v0.enc.json --passphrase "your-passphrase"
```

```bash
python scripts/encrypt-wallet.py --base58 "PHANTOM_BASE58_PRIVATE_KEY" --output /path/to/wallet.ema_pullback_2h_long_v0.enc.json --passphrase "your-passphrase"
```

## 5. ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pybot.main
```

## 6. Docker 実行

```bash
docker compose up --build
```

`bot` と `redis` の2サービスのみ起動します。

## 7. PAPER / LIVE

- `execution.mode = PAPER`
  - 送信なし
  - `models/{model_id}/paper_trades`, `models/{model_id}/paper_runs` に記録
- `execution.mode = LIVE`
  - 実際に送信
  - `models/{model_id}/trades`, `models/{model_id}/runs` に記録

## 8. 複数モデル設定

- モデル設定は `models/{model_id}/config/current` で完結
- `model_id` 単位で独立実行されます
- Firestore 設定はリアルタイムリスナーで監視し、`models/{model_id}` と `models/{model_id}/config/current` の変更を即時反映します
- 毎サイクルの Firestore 設定再読込は行いません（イベント駆動 + 15分のフォールバック再同期）
- 例:
  - `ema_pullback_2h_long_v0` (`LONG`, `ema_trend_pullback_v0`)
  - `ema_pullback_15m_both_v0` (`LONG`, `ema_trend_pullback_15m_v0`) ※戦略診断で `entry_direction` を切替
  - `storm_2h_short_v0` (`SHORT`, `storm_short_v0`)

注意:
- `SHORT` は現物の `SELL_SOL_FOR_USDC -> BUY_SOL_WITH_USDC` で実装
- ショートモデルはSOL在庫を使うため、ウォレットのSOL残高が必要

## 9. 動作確認ポイント

- `run_cycle finished` が定期出力される
- ENTRY時: `CREATED -> SUBMITTED -> CONFIRMED`
- EXIT時: `CONFIRMED -> CLOSED`
- 失敗時: `state=FAILED` と `execution.entry_error / exit_error`

## 10. VPS 移植

1. VPSにこのリポジトリを配置
2. Docker / Docker Compose をインストール
3. 認証ファイル（`secrets/firebase-service-account.json` とウォレット鍵）を配置
4. `docker compose up -d --build`
5. Firestoreコレクションを監視

### 10.1 GitHub Actions で環境変数を管理（推奨）

`.env` をVPSに手作業で置かず、`Deploy Bot` ワークフローから環境変数を直接注入できます。  
GitHub Secrets に以下を登録してください。

- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`
- `WALLET_KEY_PASSPHRASE`
- `SLACK_WEBHOOK_URL`（任意）

`SOLANA_RPC_URL` は `docker-compose.yml` で `https://api.mainnet-beta.solana.com` 固定です。  
`GOOGLE_APPLICATION_CREDENTIALS` は Docker 実行時に `/run/secrets/firebase-service-account.json` 固定です。

この設定で `main` push / 手動実行時に、Secrets がSSH経由でVPSへ渡され、そのまま `docker compose up -d --build` が実行されます。

## 11. Research（分析専用）

分析は `research/` に分離し、エントリー判定ロジックは `pybot` の戦略を直接再利用します。

- データ取得:
  - `python -m research.scripts.fetch_ohlcv --pair SOL/USDC --timeframe 2h --years 2 --output research/data/raw/solusdc_2h.csv`
  - `python -m research.scripts.fetch_ohlcv --pair SOL/USDC --timeframe 15m --years 0.5 --output research/data/raw/solusdc_15m.csv`
- バックテスト:
  - `python -m research.scripts.run_backtest --config research/models/ema_pullback_2h_long_v0/config/current.json --bars research/data/raw/solusdc_2h.csv --output research/data/processed/backtest_latest.json`

詳細は `research/README.md` を参照してください。
