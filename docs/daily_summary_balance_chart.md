# 日次サマリ Slack 通知への 1ヶ月残高水位グラフ添付 改修指示書

## 目的
日次トレード結果サマリ（JST）の Slack 通知に、各 bot（DEX / GMO）の **直近 30 日の残高水位グラフ** を画像添付する。

## 全体方針
1. **日次 balance スナップショットを Firestore に保存**（既存データから再構築できないため新規追加が必須）
2. **サマリ生成時に直近 30 件を取得 → matplotlib で PNG 生成**
3. **Slack Bot Token + `files.upload_v2` API で画像添付**（webhook はテキスト用に温存 or 廃止）

---

## P0-1. Firestore に日次 balance スナップショットを保存

### スキーマ
```
models/{model_id}/daily_balance/{YYYY-MM-DD (JST)}
{
  "snapshot_date_jst": "2026-05-09",
  "snapshot_at_iso": "2026-05-09T00:05:00+09:00",
  "balance_jpy": 123456.78,            # GMO のみ
  "balance_usdc": 1234.56,             # DEX のみ
  "balance_native_sol": 5.123,         # DEX のみ（参考値）
  "cumulative_realized_pnl_jpy": ...,  # 全 closed trade の累積（任意）
  "source": "GMO_AVAILABLE_MARGIN" | "SOLANA_WALLET",
  "model_id": "..."
}
```

`balance_jpy` / `balance_usdc` を **メイン水位指標**とし、グラフはこの値をプロット。

### 取得元
- **GMO**: `ExecutionPort.get_available_margin_jpy()` を流用（既存）。「証拠金維持率 × 取引余力」の概念整理は別途必要だが、当面は `available_margin_jpy` を水位として扱う。建玉中の含み損益は `total_realized_pnl_jpy` の累積で別途確認可能。
- **DEX**: `SolanaSender.get_spl_token_balance_ui_amount(USDC_MINT)` + `get_native_sol_balance_ui_amount()`。USDC 残高をメイン、SOL は `SOL/USDC` 価格（`quote_client` 経由）で USDC 換算した合算値も保存可。
  - PAPER モードでは仮想残高ロジックが既にあれば流用、なければスキップ（snapshot を書かない）。

### 保存タイミング
日次サマリ送信処理 [apps/dex_bot/infra/bootstrap.py:437 `_maybe_send_daily_trade_summary`](apps/dex_bot/infra/bootstrap.py#L437) の冒頭で、各 model context について snapshot を取得・保存する。

**注意**: スナップショットは「日次サマリ生成時刻 = JST 0:05〜0:15 頃」の値で取れるので、`snapshot_date_jst = target_date_jst`（前日扱い）として保存。LIVE モデルのみ対象。

### 修正ファイル
- 新規: `apps/{dex,gmo}_bot/app/ports/persistence_port.py` に `save_daily_balance(snapshot)` / `list_recent_daily_balances(days)` 追加
- 新規: `apps/{dex,gmo}_bot/adapters/persistence/firestore_repo.py` に実装
- 新規: ポート/型 `apps/{dex,gmo}_bot/domain/model/types.py` に `DailyBalanceRecord` TypedDict 追加
- 修正: `apps/dex_bot/infra/bootstrap.py` `_maybe_send_daily_trade_summary` に snapshot 書き込み処理追加

### 受入条件
- LIVE モデルでサマリ送信を 1 度走らせると `models/{model_id}/daily_balance/{YYYY-MM-DD}` ドキュメントが 1 件作成される
- 同日に再送（dedupe を貫通する経路）しても上書き更新で重複しない（doc ID = JST 日付なので `set(merge=True)`）
- PAPER モデルや残高取得失敗時は WARN ログを出してサマリ送信は継続（残高未取得を理由にサマリ自体を止めない）

---

## P0-2. balance グラフ生成モジュールの追加

### 配置
- 新規: `apps/dex_bot/infra/alerting/balance_chart.py`（共通モジュール、両 bot で利用）

### API
```python
def render_balance_chart_png(
    *,
    title: str,
    series: list[BalanceChartSeries],   # bot ごと 1 系列、複数モデルを重ねる場合は複数系列
    target_date_jst: str,
) -> bytes:
    ...
```
`BalanceChartSeries` は `label`、`unit`（"JPY" / "USDC"）、`points: list[(date_jst: str, value: float)]` を持つ dataclass。

### 描画仕様
- 横軸: 日付（直近 30 日、欠損日は前日値で線を引かず点を打たない＝ギャップ）
- 縦軸: 残高（unit に合わせフォーマット、JPY は整数、USDC は小数 2 桁）
- 線色: bot ごとに固定（GMO=青、DEX=緑）
- タイトル: `Balance trend (last 30 days, JST)`
- グリッド ON、凡例 ON、画像サイズ 1200×600px、DPI 100、PNG bytes で返す
- 系列が空（snapshot 1 件未満）の場合は `(0 件, グラフ非表示)` のテキストプレースホルダを返さず `None` を返し、呼び出し側で添付スキップ

### 依存追加
- `matplotlib` を `pyproject.toml` の dependencies に追加（GUI 不要のため `matplotlib.use("Agg")` を必ず冒頭で実行）
- フォントは英数字のみ使用してフォント警告を回避（タイトル・凡例も英語）

### 受入条件
- 30 件のサンプルから期待されたサイズの PNG bytes が返るユニットテスト
- 空入力で `None` を返すユニットテスト
- `matplotlib.use("Agg")` がモジュールトップで実行されていることを assert（test）

---

## P0-3. Slack Bot Token + `files.upload_v2` で画像添付

### 環境変数追加
- `SLACK_BOT_TOKEN`: `xoxb-...`（File 添付用、`files:write` スコープ必須）
- `SLACK_DAILY_SUMMARY_CHANNEL_ID`: 投稿先チャンネル ID（`C0123ABCDE` 形式）
  - Webhook は引き続きエラー通知などテキスト系で使用するため温存

### `SlackNotifier` の拡張
- 新規メソッド `notify_combined_daily_trade_summary_with_charts_jst(...)`:
  ```python
  def notify_combined_daily_trade_summary_with_charts_jst(
      *,
      dex_report,
      gmo_report,
      dex_chart_png: bytes | None,
      gmo_chart_png: bytes | None,
  ) -> None
  ```
- 実装は **Slack Web API** (`https://slack.com/api/chat.postMessage` + `files.upload_v2`) を使用。
- フロー:
  1. `chat.postMessage` でテキストサマリを投稿（thread_ts 取得）
  2. 各 PNG を `files.upload_v2` で同チャンネル + `thread_ts` で添付（スレッド内に画像 2 枚）
- **API 失敗時は warn ログ + テキスト送信のみフォールバック**（チャート無し送信に劣化）
- bot token / channel id 未設定なら従来の webhook 経路にフォールバック

### dedupe
既存の dedupe key (`daily_summary_jst:{target_date_jst}`) を流用。チャート添付ありの新ルートも同じ key で重複抑制。

### 修正ファイル
- `apps/dex_bot/infra/alerting/slack_notifier.py`: 新メソッド追加、`SlackAlertConfig` に `bot_token` / `daily_summary_channel_id` 追加
- `apps/dex_bot/infra/config/env.py`: 新環境変数読み込み
- `apps/dex_bot/infra/bootstrap.py` `_maybe_send_daily_trade_summary`: 新メソッドを優先呼び出し、PNG 生成失敗時は既存 `notify_combined_daily_trade_summary_jst` にフォールバック

### 受入条件
- Bot Token + channel ID が設定されているとき、`chat.postMessage` 1 件 + `files.upload_v2` 2 件が呼ばれる（モックテスト）
- 設定欠落時は webhook 経路にフォールバックすることをテスト
- API エラー時はテキスト送信のみで完了することをテスト

---

## P1-1. balance snapshot 取得の race / failure handling

- snapshot 取得 (`get_available_margin_jpy` / `get_spl_token_balance_ui_amount`) はネットワーク I/O を伴うため、各 model 独立に try/except し、失敗してもサマリ送信は継続。
- snapshot 失敗時は当日の系列にギャップが入るのみ（前日値で埋めない）。
- DEX は SOL/USDC レート取得 (`quote_client`) も失敗し得るため、`balance_usdc = USDC残高 のみ` でフォールバック保存（`balance_native_sol_in_usdc` を nullable に）。

## P1-2. 過去データの後埋め（任意）

既に運用が稼働している場合、初日はグラフが 1 点しか描けない。必要に応じて:
- 既存 closed trades の `total_realized_pnl_jpy` 累積から「実現 PnL ベースの推定残高推移」を逆算し、初回投入時に過去 30 日ぶん seed する。
- または、最低 30 日待って初回フル描画とする運用判断（シンプル）。

推奨は **後者**（コード追加なし）。

## P2-1. 累積実現 PnL のセカンダリ系列（任意）

メイン残高線に加えて、`cumulative_realized_pnl` を破線で重ねると「入出金変動」と「PnL 変動」が分離できて運用判断しやすい。スコープ拡大なので別タスク化推奨。

---

## 影響度サマリ

| 項目 | 役割 | 優先 |
|---|---|---|
| P0-1 daily_balance Firestore 保存 | データソース新設 | 必須 |
| P0-2 chart 生成モジュール | 画像生成 | 必須 |
| P0-3 Bot Token + files.upload_v2 | 画像投稿経路 | 必須 |
| P1-1 失敗時 fallback | 堅牢性 | 必須 |
| P1-2 過去データ後埋め | 初日 UX | 任意 |
| P2-1 累積 PnL 重ね描き | UX 拡張 | 任意 |

---

## 必要な前準備（運用側）

1. Slack App に Bot User を追加し `files:write` / `chat:write` スコープを付与
2. Bot を投稿先チャンネルに招待
3. `SLACK_BOT_TOKEN` / `SLACK_DAILY_SUMMARY_CHANNEL_ID` を `.env` および本番環境変数に追加
4. `pyproject.toml` に `matplotlib` を追加 → `pip install -r ...`

## 受入テスト追加方針

P0 修正に伴い:
1. `save_daily_balance` / `list_recent_daily_balances` Firestore 動作テスト（モックで十分）
2. `render_balance_chart_png` のスモークテスト（PNG bytes が返る、空入力で None）
3. `SlackNotifier.notify_combined_daily_trade_summary_with_charts_jst` で chat.postMessage / files.upload_v2 が呼ばれること（モック）
4. bootstrap 統合テスト: snapshot 失敗 → サマリは送信され、グラフ無しメッセージにフォールバック

## 実装規模見積もり

- 新規コード: 約 250〜350 行
- テスト: 約 150〜200 行
- 依存追加: matplotlib のみ
- 環境変数: 2 件
- 想定工数: 0.5〜1 人日
