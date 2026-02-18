# crypto_trade_bot (v0)

Solana 現物自動売買Bot（`SOL/USDC`、`LONG_ONLY`、`2h` デフォルト）の v0 実装です。

- エントリー: 設定タイムフレーム（`2h` / `4h`）のクローズ時に `EMA20 > EMA50`
- 損切り: 直近 `N` 本のスイング安値
- 利確: `2R` 全決済
- 実行: Jupiter API で swap tx 生成、Solana 署名送信
- 永続化: Firestore（`config/current`, `trades`, `runs`）
- 同時実行/重複防止: Redis（`lock:runner`, `idem:entry:*`, `tx:inflight:*`）

## 1. 前提

- Node.js 20+
- npm
- Redis（ローカルまたは Docker）
- Firestore（サービスアカウントJSON）

## 2. 環境変数（5個のみ）

`.env.example` を `.env` にコピーして設定します。

- `SOLANA_RPC_URL`
- `REDIS_URL`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `WALLET_KEY_PATH`
- `WALLET_KEY_PASSPHRASE`

`GOOGLE_APPLICATION_CREDENTIALS` と `WALLET_KEY_PATH` は相対パス/絶対パスどちらでも利用できます。
相対パスの場合は `docker-compose.yml` があるプロジェクトルート基準です。

## 3. Firestore 事前準備

### 3.1 コレクション

- `config`（ドキュメント: `current`）
- `trades`
- `runs`

### 3.2 `config/current` の投入

以下をそのまま保存してください。

```json
{
  "enabled": true,
  "network": "mainnet-beta",
  "pair": "SOL/USDC",
  "direction": "LONG_ONLY",
  "signal_timeframe": "2h",
  "strategy": {
    "name": "ema_trend_pullback_v0",
    "ema_fast_period": 20,
    "ema_slow_period": 50,
    "swing_low_lookback_bars": 12,
    "entry": "ON_BAR_CLOSE"
  },
  "risk": {
    "max_loss_per_trade_pct": 0.5,
    "max_trades_per_day": 3
  },
  "execution": {
    "mode": "PAPER",
    "swap_provider": "JUPITER",
    "slippage_bps": 100,
    "min_notional_usdc": 50,
    "only_direct_routes": false
  },
  "exit": {
    "stop": "SWING_LOW",
    "take_profit_r_multiple": 2.0
  },
  "meta": {
    "config_version": 1,
    "note": "v0: spot swap only, long only, 2h close entry, TP=2R all, notify=none"
  }
}
```

または seeder を使って投入:

```bash
npm run seed-config
```

`LIVE` で投入したい場合:

```bash
npm run seed-config -- --mode LIVE
```

## 4. Wallet 準備（Phantom 連携前提）

1. Bot専用ウォレットを作成（例: Solana CLI の `id.json`）
2. 秘密鍵を暗号化ファイルに変換

```bash
npm run encrypt-wallet -- --input /path/to/id.json --output /path/to/wallet.enc.json --passphrase "your-passphrase"
```

Phantom でエクスポートした base58 秘密鍵を使う場合:

```bash
npm run encrypt-wallet -- --base58 "PHANTOM_BASE58_PRIVATE_KEY" --output /path/to/wallet.enc.json --passphrase "your-passphrase"
```

3. `WALLET_KEY_PATH` に暗号化ファイル、`WALLET_KEY_PASSPHRASE` に同じパスフレーズを設定
4. 同じ秘密鍵を Phantom に import して監視/入出金を行う

## 5. PAPER運用（推奨）

`execution.mode = "PAPER"` のとき、実売買は行いません。

- 記録先: `paper_trades`, `paper_runs`
- Tx送信: なし（Jupiter quoteベースのシミュレーション）
- `tx_signature`: `PAPER_<UUID>`

起動:

```bash
docker compose up --build
```

確認:

- `paper_runs/{run_id}` にタイムフレーム判定結果が残る
- `paper_trades/{trade_id}` に state遷移と `execution.order` / `execution.result` が残る

## 6. LIVE移行手順

1. `config/current.execution.mode` を `LIVE` に変更
2. Botを再起動（`docker compose up -d --build`）
3. 記録先が `trades`, `runs` に切り替わることを確認

## 7. ローカル起動（Node実行）

```bash
npm install
npm run test
npm run build
npm run dev
```

## 8. Docker 起動

```bash
docker compose up --build
```

`bot` と `redis` の 2 サービスのみ起動します。

`GOOGLE_APPLICATION_CREDENTIALS` と `WALLET_KEY_PATH` はホスト上の実ファイルを
bind mount で bot に渡します（相対パス可）。

## 9. 動作確認ポイント

- `runs/{run_id}` に 5分ごとの実行結果が記録される
- シグナル成立時に `trades/{trade_id}` が `CREATED -> SUBMITTED -> CONFIRMED` へ遷移
- エグジット時に `CONFIRMED -> CLOSED` へ遷移
- 失敗時は `FAILED` と `execution.entry_error` / `execution.exit_error` が記録される

## 10. VPS 移植（docker compose）

1. このリポジトリを VPS に配置
2. VPS に Docker / Docker Compose を導入
3. `.env` と認証ファイル（GCP JSON / encrypted wallet）を同じパスで配置
4. `docker compose up -d --build`
5. Firestore `runs` / `trades` を監視
