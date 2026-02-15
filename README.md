# crypto_trade_bot (v0)

Solana 現物自動売買Bot（`SOL/USDC`、`LONG_ONLY`、`4h`）の v0 実装です。

- エントリー: 4h クローズ時に `EMA20 > EMA50`
- 損切り: 直近 `N` 本のスイング安値
- 利確: `2R` 全決済
- 実行: Jupiter API で swap tx 生成、Solana 署名送信
- 永続化: Firestore（`config/current`, `trades`, `runs`）
- 同時実行/重複防止: Redis（`lock:runner`, `idem:signal:*`, `tx:inflight:*`）

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
  "signal_timeframe": "4h",
  "strategy": {
    "name": "ema_trend_pullback_v0",
    "ema_fast_period": 20,
    "ema_slow_period": 50,
    "swing_low_lookback_bars": 12,
    "entry": "ON_4H_CLOSE"
  },
  "risk": {
    "max_loss_per_trade_pct": 0.5,
    "max_trades_per_day": 3
  },
  "execution": {
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
    "note": "v0: spot swap only, long only, 4h close entry, TP=2R all, notify=none"
  }
}
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

## 5. ローカル起動（Node実行）

```bash
npm install
npm run test
npm run build
npm run dev
```

## 6. Docker 起動

```bash
docker compose up --build
```

`bot` と `redis` の 2 サービスのみ起動します。

`GOOGLE_APPLICATION_CREDENTIALS` と `WALLET_KEY_PATH` はホスト上の実ファイルを volume mount で bot に渡します。

## 7. 動作確認ポイント

- `runs/{run_id}` に 5分ごとの実行結果が記録される
- シグナル成立時に `trades/{trade_id}` が `CREATED -> SUBMITTED -> CONFIRMED` へ遷移
- エグジット時に `CONFIRMED -> SUBMITTED -> CLOSED` へ遷移
- 失敗時は `FAILED` と `execution.entry_error` / `execution.exit_error` が記録される

## 8. VPS 移植（docker compose）

1. このリポジトリを VPS に配置
2. VPS に Docker / Docker Compose を導入
3. `.env` と認証ファイル（GCP JSON / encrypted wallet）を同じパスで配置
4. `docker compose up -d --build`
5. Firestore `runs` / `trades` を監視
