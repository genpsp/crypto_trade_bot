# gmo_bot パフォーマンス分析レポート機能 設計書

## 1. 目的・背景

`apps/gmo_bot` で稼働中のモデルについて、Firestoreに蓄積された取引履歴・資産推移・判断ログから、
**成績・敗因・勝因**を一覧できるレポートをオンデマンドで生成する機能を追加する。

既存の [apps/gmo_bot/infra/alerting/daily_trade_summary.py](../apps/gmo_bot/infra/alerting/daily_trade_summary.py)
はSlack向けの1行要約に留まっており、戦略チューニング判断に必要な深い分析ができていない。

### 想定ユースケース

- **誰が**: 開発者が手元で実行
- **何のため**: 稼働中モデルの成績、資産推移、勝因/敗因傾向を1ファイルで把握
- **頻度**: オンデマンド（CLI）、必要に応じてSlack添付で共有

---

## 2. 設計判断サマリー（確定事項）

| 項目 | 採用 | 理由 |
|---|---|---|
| 出力形式 | **HTML 1ファイル完結**（PNG base64埋め込み） | ブラウザ単独で閲覧・共有可能 |
| 実行モード | **CLI + Slack添付**（`--slack` フラグ） | 手元実行を主、共有を副 |
| モデルスコープ | **1モデル/レポート** | シンプル、複数は別実行で生成 |
| 追加依存 | **pandas + Jinja2** | 集計・テンプレで生産性が桁違い、researchと揃う |
| 既存依存 | matplotlib / google-cloud-firestore / pyarrow | 流用 |

---

## 3. アーキテクチャ

既存のヘキサゴナル構造（domain / app / adapters / infra）を踏襲し、reporting slice を追加する。

```
apps/gmo_bot/
├── app/reporting/                       # ユースケース層（新規）
│   ├── __init__.py
│   ├── generate_report.py               # GenerateReportUseCase オーケストレータ
│   ├── dataset.py                       # PerformanceDataset (frozen dataclass)
│   ├── metrics.py                       # ReportMetrics 計算
│   └── attribution.py                   # 勝因/敗因 attribution
├── adapters/persistence/
│   └── firestore_repo.py                # 拡張: range query 3メソッド追加
└── infra/reporting/                     # 出力層（新規）
    ├── chart_renderer.py                # matplotlib → PNG bytes
    ├── html_renderer.py                 # Jinja2 + base64画像埋め込み
    └── templates/report.html.j2
```

CLIエントリ:

```
apps/gmo_bot/reports.py                  # python -m apps.gmo_bot.reports
```

### 依存方向

```
reports.py (CLI)
   ↓
app/reporting/generate_report.py
   ↓                    ↓
adapters/persistence    infra/reporting
(FirestoreRepo)         (chart, html)
```

ドメイン層には触れず、既存型（[TradeRecord/RunRecord/DailyBalanceRecord](../apps/gmo_bot/domain/model/types.py)）を読み取り専用で利用。

---

## 4. データソース

すべて Firestore に既存。`mode` (live/paper) でコレクション接頭辞を切り替える。

| データ | コレクションパス | 主要フィールド | 用途 |
|---|---|---|---|
| 取引履歴 | `models/{id}/trades/{YYYY-MM-DD}/items/*` | `state`, `direction`, `close_reason`, `execution.*`, `position.*`, `signal.*` | 損益・勝率・attribution |
| 判断ログ | `models/{id}/runs/{YYYY-MM-DD}/items/*` | `result`, `reason`, `summary`, `metrics` | NO_SIGNAL/SKIPPED/FAILED分析 |
| 資産推移 | `models/{id}/daily_balance/{YYYY-MM-DD}` | `balance_total_usdc`, `cumulative_realized_pnl_jpy` | エクイティ・DDチャート |

paperモード時は `paper_trades` / `paper_runs` 接頭辞に切替。

### Range Query 実装方針

Firestore SDK にはレンジクエリ非対応のため、日付パーティションを **`asyncio.gather` で並列スキャン**する。

```python
# adapters/persistence/firestore_repo.py に追加するメソッド
async def list_trades_in_range(model_id, from_jst, to_jst, mode) -> list[TradeRecord]
async def list_runs_in_range(model_id, from_jst, to_jst, mode) -> list[RunRecord]
async def list_daily_balances_in_range(model_id, from_jst, to_jst, mode) -> list[DailyBalanceRecord]
```

---

## 5. CLI 仕様

```bash
python -m apps.gmo_bot.reports \
  --model-id gmo_ema_pullback_15m_both_v0 \
  --from 2026-04-01 \
  --to 2026-05-15 \
  --mode live \
  --output ./reports/2026-05-15_gmo_ema_pullback_15m.html \
  [--slack] \
  [--slack-channel CHANNEL_ID]
```

| 引数 | 必須 | 説明 |
|---|---|---|
| `--model-id` | ◯ | 対象モデルID |
| `--from` / `--to` | ◯ | JST基準の日付（YYYY-MM-DD）。`to` は当日まで含む |
| `--mode` | × | `live` / `paper`（デフォルト: `live`） |
| `--output` | × | 出力HTMLパス（デフォルト: `./reports/{date}_{model_id}.html`） |
| `--slack` | × | 指定時にSlack添付 |
| `--slack-channel` | × | Slack送信先（省略時は既存 `SlackNotifier` のデフォルト） |

### Slack 添付フロー

既存の [apps/gmo_bot/infra/alerting/slack_notifier.py](../apps/gmo_bot/infra/alerting/slack_notifier.py) を再利用:

1. HTMLをローカルに保存
2. 主要KPI（勝率・Net PnL・最大DD）をテキスト投稿
3. 同スレッドにHTMLを `files.upload_v2` で添付
   - 既存の [apps/dex_bot/infra/alerting/balance_chart.py](../apps/dex_bot/infra/alerting/balance_chart.py) でbytesアップロード実績あり、踏襲

---

## 6. レポートHTML構造

```
[ヘッダ] model_id / 期間 / mode / 生成日時

§A サマリーカード（KPI 8項目）
   - 開始残高 / 終了残高 / Net PnL (JPY) / 累積リターン %
   - 総取引数 / 勝率 / Profit Factor / 平均RR
   - 最大DD / 最長連敗 / Sharpe（日次PnLベース）

§B 資産推移チャート
   - エクイティカーブ（折線）
   - ドローダウンチャート（エリア）
   - 日次PnL棒グラフ（緑/赤）

§C 取引分析
   - 損益分布ヒストグラム（勝ち/負け重ね）
   - 保有時間分布（winners vs losers）
   - エントリー時刻ヒートマップ（曜日×時間帯 JST）
   - 方向別成績テーブル（LONG / SHORT）

§D 勝因・敗因（attribution）
   - close_reason別集計（TP / SL / MANUAL / SYSTEM_ERROR）
   - エントリー時シグナル分布の箱ひげ比較
     - RSI / EMA gap / ATR を勝ち vs 負けで対比
     - → 閾値見直しの示唆を引き出す
   - スリッページ分布（bps、winners vs losers）
   - 連敗クラスタ（3連敗以上をテーブル化）

§E 判断ログ分析（runs）
   - no_signal_reason_counts TOP10
   - SKIPPED_ENTRY 理由別件数
   - FAILED ランのタイムライン

§F 全取引リスト（<details>折りたたみ）
   - entry/exit time, direction, qty, prices, fees, PnL, close_reason, slippage_bps
```

---

## 7. 計算ロジックの注意点

### 7.1 PnL 計算（既存ロジック流用）

[apps/gmo_bot/infra/bootstrap.py:302-346](../apps/gmo_bot/infra/bootstrap.py#L302) の優先順を踏襲:

1. `execution.exit_leg_realized_pnl_jpy`（分割決済対応）
2. なければ `execution.exit_result.realized_pnl_jpy`
3. 最終フォールバック: entry/exit price から再計算

→ **`app/reporting/metrics.py` に共通ヘルパーを切り出し**、bootstrap側もそれを呼ぶようリファクタすると重複が消える。

### 7.2 JST境界

日次集計はすべて JST (UTC+9) 基準。既存の [daily_trade_summary.py](../apps/gmo_bot/infra/alerting/daily_trade_summary.py) のJST日付計算を共通化して再利用する。

### 7.3 TypedDict の部分性

`TradeRecord` のネスト要素はすべて `total=False` の TypedDict のため、`.get()` を必ず通す。
incomplete trade（state=SUBMITTED で確定前）も含まれうるので、metrics計算前に
`state == "CLOSED"` でフィルタする。

### 7.4 paper vs live

`--mode` で `paper_trades` / `paper_runs` 接頭辞に切替。混在不可（別レポート）。

---

## 8. 追加依存

| ライブラリ | バージョン | 用途 |
|---|---|---|
| `pandas` | `>=2.2` | 集計・時系列・ピボット |
| `jinja2` | `>=3.1` | HTMLテンプレ |
| `numpy` | (pandas経由) | 数値計算 |

`matplotlib>=3.9`, `pyarrow>=16.0.0`, `google-cloud-firestore` は既存依存。

---

## 9. 実装フェーズ

| Phase | 内容 | 完了基準 | 工数感 |
|---|---|---|---|
| **P1** | `FirestoreRepo` range query 拡張 + `PerformanceDataset` 構築 | trades/runs/balancesをpandas DataFrameに詰める | 1日 |
| **P2** | `MetricsCalculator` + §A サマリーカードのHTML骨子 | KPI 8項目が表示 | 1日 |
| **P3** | `ChartRenderer` §B 資産推移3チャート | エクイティ/DD/日次PnL | 1日 |
| **P4** | §C 取引分析 | ヒストグラム/ヒートマップ/方向別表 | 0.5日 |
| **P5** | §D 勝因/敗因 attribution | RSI/EMA gap/ATR分布比較・連敗 | 1日 |
| **P6** | §E 判断ログ + §F 全取引表 | runs集計・取引明細 | 0.5日 |
| **P7** | Slack添付 + CLI仕上げ | `--slack` で投稿成功 | 0.5日 |

合計: **約5.5日**

---

## 10. テスト方針

- **ユニットテスト** (`tests/app/reporting/`):
  - `metrics.py`: 既知のTradeRecordフィクスチャから勝率/PnL/DD/Sharpeを検証
  - `attribution.py`: close_reason別集計・連敗検出
- **統合テスト**: Firestoreエミュレータ or モックで小規模データセットを流す
- **ゴールデンファイル**: 生成HTMLを `tests/fixtures/expected_report.html` と比較（チャート部はメトリクス値のみ検証）

---

## 11. 将来拡張（スコープ外、メモのみ）

- 複数モデル横断レポート（cross-model比較）
- 日次バッチ化（cron/Cloud Scheduler）→ GCSに自動アップロード
- Plotly インタラクティブ版（zoom/hover）
- backtest結果との並列比較（[research/](../research/) の成果物と並べる）
- 戦略パラメータ感度分析（同モデル別バージョンの成績比較）

---

## 12. 関連ファイル参照

| 役割 | ファイル |
|---|---|
| 取引データ型 | [apps/gmo_bot/domain/model/types.py](../apps/gmo_bot/domain/model/types.py) |
| 既存Firestore永続化 | [apps/gmo_bot/adapters/persistence/firestore_repo.py](../apps/gmo_bot/adapters/persistence/firestore_repo.py) |
| 既存日次サマリ | [apps/gmo_bot/infra/alerting/daily_trade_summary.py](../apps/gmo_bot/infra/alerting/daily_trade_summary.py) |
| PnL算出ロジック（流用元） | [apps/gmo_bot/infra/bootstrap.py:302-346](../apps/gmo_bot/infra/bootstrap.py#L302) |
| 既存チャート実装（参考） | [apps/dex_bot/infra/alerting/balance_chart.py](../apps/dex_bot/infra/alerting/balance_chart.py) |
| 戦略パラメータ | [apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py) |
