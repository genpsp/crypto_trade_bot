# crypto_trade_bot (Python v1)

Node.js 実装を Python に全面移行した Solana 現物自動売買Bot です。  
レイヤ分離は `domain / app / adapters / infra` のまま維持しています。

- エントリー: 設定タイムフレーム（`2h` / `4h`）のクローズ時
- ロングのみ: `SOL/USDC`
- 損切り: スイング安値 + `max_loss_per_trade_pct` で締める
- 利確: `R` 倍（`take_profit_r_multiple`）
- 実行: Jupiter quote/swap + Solana署名送信
- 永続化: Firestore（`config/current`, `trades/runs` or `paper_trades/paper_runs`）
- 重複防止: Redis（`lock:runner`, `idem:entry:*`, `tx:inflight:*`）

## 1. 前提

- Python 3.12+
- pip
- Docker / Docker Compose
- Firestore サービスアカウントJSON

## 2. 環境変数（5個のみ）

`.env.example` を `.env` にコピーして設定:

- `SOLANA_RPC_URL`
- `REDIS_URL`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `WALLET_KEY_PATH`
- `WALLET_KEY_PASSPHRASE`

`GOOGLE_APPLICATION_CREDENTIALS` と `WALLET_KEY_PATH` は相対/絶対パスどちらでも可。  
相対パスは `docker-compose.yml` があるプロジェクトルート基準です。

## 3. Firestore 事前準備

### 3.1 コレクション

- `config/current`
- `trades`, `runs`（LIVE）
- `paper_trades`, `paper_runs`（PAPER）

`runs` / `paper_runs` は日付で分割して保存します:

- `runs/{YYYY-MM-DD}/items/{run_doc_id}`（LIVE）
- `paper_runs/{YYYY-MM-DD}/items/{run_doc_id}`（PAPER）

同日・同理由の `SKIPPED` / `SKIPPED_ENTRY` は新規作成せず、同じ `run_doc_id` を更新して `occurrence_count` を加算します。

### 3.2 config/current 投入

```bash
python scripts/seed-firestore-config.py --mode PAPER
```

LIVE投入:

```bash
python scripts/seed-firestore-config.py --mode LIVE
```

## 4. Wallet 準備（Phantom連携）

`id.json` または Phantom base58 秘密鍵を暗号化:

```bash
python scripts/encrypt-wallet.py --input /path/to/id.json --output /path/to/wallet.enc.json --passphrase "your-passphrase"
```

```bash
python scripts/encrypt-wallet.py --base58 "PHANTOM_BASE58_PRIVATE_KEY" --output /path/to/wallet.enc.json --passphrase "your-passphrase"
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
  - `paper_trades`, `paper_runs` に記録
- `execution.mode = LIVE`
  - 実際に送信
  - `trades`, `runs` に記録

## 8. 動作確認ポイント

- `run_cycle finished` が定期出力される
- ENTRY時: `CREATED -> SUBMITTED -> CONFIRMED`
- EXIT時: `CONFIRMED -> CLOSED`
- 失敗時: `state=FAILED` と `execution.entry_error / exit_error`

## 9. VPS 移植

1. VPSにこのリポジトリを配置
2. Docker / Docker Compose をインストール
3. `.env` と認証ファイルを配置
4. `docker compose up -d --build`
5. Firestoreコレクションを監視

## 10. Research（分析専用）

分析は `research/` に分離し、エントリー判定ロジックは `pybot` の戦略を直接再利用します。

- データ取得:
  - `python -m research.scripts.fetch_ohlcv --pair SOL/USDC --timeframe 2h --years 2 --output research/data/raw/solusdc_2h.csv`
- バックテスト:
  - `python -m research.scripts.run_backtest --config research/config.json --bars research/data/raw/solusdc_2h.csv --output research/data/processed/backtest_latest.json`

詳細は `research/README.md` を参照してください。
