# gmo_ema_pullback_15m_both_v0 改善プラン

## 0. ドキュメントの位置づけ

[gmo_bot パフォーマンス分析レポート機能](./gmo_bot_performance_report.md) で生成した
**2026-04-15 ～ 2026-05-15（直近1ヶ月、LIVE）** のレポート結果に基づく、
モデル `gmo_ema_pullback_15m_both_v0` の改善計画。

レポート本体: [reports/2026-05-15_gmo_ema_pullback_15m_both_v0.html](../reports/2026-05-15_gmo_ema_pullback_15m_both_v0.html)

戦略実装: [apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py)

---

## 1. 現状サマリー（事実）

### 1.1 損益

| 指標 | 値 |
|---|---:|
| Net PnL | **-872 JPY** |
| Gross PnL | -640 JPY |
| Fees | 232 JPY |
| Profit Factor | **0.67** |
| Sharpe (daily, ann.) | **-3.33** |
| 勝率 | 27.8% (10W / 26L) |
| Avg R:R | 1.75 |
| 損益分岐勝率（理論値） | 36.4% = 1 / (1 + 1.75) |
| 最大ドローダウン | -1,243 JPY |
| 最長連敗 | 8 |
| 平均保有時間 | 約461分（7.7時間）|

→ **R:R は設計通りだが、現実の勝率が損益分岐に8pt 不足**。期間中の戦略エッジが負側。

### 1.2 方向別

| Direction | n | 勝率 | Sum PnL | Avg PnL |
|---|---:|---:|---:|---:|
| LONG | 28 | 35.7% | **+17 JPY** | +1 |
| **SHORT** | **8** | **0.0%** | **-657 JPY** | -82 |

→ 期間損失 -872 のうち **約75%が SHORT 由来**。LONG はほぼフラット。

### 1.3 決済理由別

| close_reason | n | 勝率 | Sum PnL | Avg PnL |
|---|---:|---:|---:|---:|
| TAKE_PROFIT | 9 | 100% | +1,153 | +128 |
| **STOP_LOSS** | **26** | **0%** | **-1,962** | -75 |
| MANUAL | 1 | 100% | +169 | +169 |

- SL ヒット率: **74%**（除く MANUAL）
- TP:SL ≒ **1:2.9**

### 1.4 連敗クラスタ（3連以上）

| 期間 | 連敗数 | 累計損失 |
|---|---:|---:|
| 04/18–04/26 | **8** | -607 |
| 04/27–05/04 | 5 | -414 |
| 05/06–05/08 | 4 | -224 |
| 05/09–05/10 | 3 | -177 |
| 05/12–05/14 | 4 | -358 |

→ **ほぼ毎週 3+ 連敗が発生**。市場レジーム適合性に疑問。

### 1.5 シグナル分析

| 指標 | Winners (n=10) | Losers (n=26) | 差分 |
|---|---:|---:|---:|
| ema_fast | 13,843 | 14,065 | -1.6% |
| ema_slow | 13,811 | 14,039 | -1.6% |
| entry slippage (bps) | 3.5 | 3.8 | 同等 |

→ **勝ち手は約1.5%低い価格帯で発生**。
   高値圏（上昇トレンド後半 pullback）でのエントリーが負けやすい傾向。

### 1.6 オペレーショナル指標

| 指標 | 値 | 備考 |
|---|---:|---|
| 期間内 logged runs | 5,713 | |
| うち FAILED | **5,632 (98.6%)** | 要調査（市場データ取得失敗の蓄積？） |
| OPENED | 36 | |
| SKIPPED | 28 | |
| CLOSED | 17 | |
| 平均スリッページ | 入 3.7 / 出 5.0 bps | 正常範囲 |

### 1.7 データ品質の問題

- **daily_balance snapshot が 5日分のみ**（05/10 – 05/14）→ 期間全体のエクイティ曲線が描けない
- `cumulative_realized_pnl_jpy` が全て None
- → レポート機能と並行して、永続化ロジックの修正が必要

---

## 2. 評価

### 2.1 致命度

- 損失は資産の約 **-3%**（9,068 → 8,811 JPY）
- 致命的な暴落・バグ起因ではない
- 戦略のエッジが現実の市場で機能していない、というのが本質

### 2.2 統計的有意性

- closed=36、特に SHORT=8 は **サンプルとして弱い**
- ただし PF 0.67 / Sharpe -3.33 / SL率 74% は偶然では説明しにくい
- backtest 結果との突合で「現在のレジームが想定外なのか、設計自体が無効か」を切り分けが必要

### 2.3 結論

**現状の運用を継続するべきではない。最低限 SHORT は即停止。**
LONG はトン (+17 JPY) なのでフィルタ追加で改善余地あり。

---

## 3. アクションプラン

### A. 即対応（今週中）

#### A-1. SHORT 方向を停止

- **理由**: 0勝8敗、期間損失の75%源
- **方法**: Firestore `models/gmo_ema_pullback_15m_both_v0/config/current` の `direction` を `BOTH` → `LONG` に変更
- **影響**: ホットリロード対応済みなので再起動不要
- **観測ポイント**: 停止後2週間の LONG-only の成績で再評価

#### A-2. FAILED ラン大量発生の原因調査

- **状況**: logged runs の98.6%が FAILED
- **調査軸**:
  - market data maintenance window（GMOの定期メンテ時間帯）と一致するか
  - GMO API のエラー応答（err-5201 等）の蓄積か
  - 既存の `is_market_data_maintenance_result` で除外されているはずだが、Firestoreには記録されている
- **対応**: ログ内訳をクエリして reason のヒストグラム化。必要なら `save_run` の保存条件を絞る

---

### B. 短期改善（2-4週間）

#### B-1. ATR_STOP_MULTIPLIER の調整

- **現状**: 1.5（[ema_trend_pullback_15m_v0.py](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py) 参照）
- **仮説**: 1.5 ATR は SOL/JPY の 15分足ボラに対して狭く、ノイズで SL を狩られている
- **検証**: [research/scripts/analyze_gmo_15m_param_sweep.py](../research/scripts/analyze_gmo_15m_param_sweep.py) で
  - `ATR_STOP_MULTIPLIER ∈ {1.5, 1.8, 2.0, 2.5, 3.0}` をスウィープ
  - 同時に `TAKE_PROFIT_R_MULTIPLE` を `{1.5, 1.8, 2.0, 2.5}` で交差
- **採用基準**: PF >= 1.2、勝率 >= 40%、最大DDが直近1ヶ月の実測 -1,243 を超えない

#### B-2. 高値圏 LONG エントリーの除外

- **仮説**: 勝ち手は EMA 値が負け手より 1.5% 低い → 高値圏のエントリーが負けやすい
- **実装案**:
  - 上位足（4h）EMA との乖離率にキャップを設ける
    - 例: `abs(current_price - ema_4h_slow) / ema_4h_slow > 5%` ならエントリー見送り
  - 既存の「上位TF trend gap チェック」を強化する形で実装
- **検証**: research basin で同期間の hindsight 検証 → 「もし除外したら何件のエントリーが消え、PnLどうなったか」

#### B-3. 連敗ストッパー

- **目的**: 1週間で 8連敗するような事故を回避
- **実装案**:
  - `N回連続 SL` で **クールダウン**（次の bar close を S個スキップ）
  - 例: 3連敗で 4時間（16本）entry skip
- **メリット**: 連敗クラスタの平均損失 -300 JPY/cluster を半減できる可能性
- **デメリット**: 反転局面の取り逃し → 必ず backtest で副作用を測る

---

### C. 中期検証（1-2ヶ月）

#### C-1. backtest と LIVE 結果の差分分析

- **必要なもの**: 同一期間 2026-04-15 ~ 2026-05-15 の backtest 結果
- **観点**:
  - LIVE 実測 vs backtest 予測 の trade-by-trade 差分
  - 約定スリッページ・遅延の影響
  - 「backtest では勝てているのに LIVE で負けている」のか
  - 「backtest からして既に負けている」のか
- **どちらかで対応分岐**:
  - backtest 勝ち / LIVE 負け → 執行品質の改善
  - 両方負け → 戦略設計の見直し（再学習）

#### C-2. SHORT 戦略の再設計判断

- A-1 で停止した SHORT を、別モデル `gmo_ema_pullback_15m_short_v1` として再構築するか判断
- 現行 `storm_short_v0` との重複・住み分け検討
- 単純な「LONG の逆」では機能していないことが今回判明。SHORT 専用のエントリー条件が必要

#### C-3. パラメータ自動最適化フロー

- 現状は手動 sweep → 結果評価 → 手動デプロイ
- レポート機能と組み合わせて「直近Nヶ月の LIVE 結果で PF < 1.0 が継続したら自動アラート」を追加
- 既存 `daily_trade_summary` の Slack 通知に「直近30日 PF」を含めるのが軽量

---

### D. データ・観測性の修正（並行）

#### D-1. daily_balance snapshot の継続記録

- 現状: 05/10 ～ 05/14 の 5日分のみ存在
- 5/10 以前のスナップショットが無いのは、機能リリースが新しい or 障害がある
- スケジューラ（[apps/gmo_bot/infra/scheduler/](../apps/gmo_bot/infra/scheduler/)）と `save_daily_balance` 呼び出し箇所を確認

#### D-2. cumulative_realized_pnl_jpy の記録

- レコードフィールド自体は [TradeRecord / DailyBalanceRecord](../apps/gmo_bot/domain/model/types.py) に存在するが None
- snapshot 生成時に累積を計算して埋める処理が抜けている可能性
- レポートの **§B エクイティカーブが balance_jpy にフォールバック中**

#### D-3. FAILED runs の保存条件見直し

- 5,632件 / month は明らかにノイズ過多
- `firestore_repo.save_run` で `result == "FAILED"` のうち市場メンテ起因のものは保存しない方向で
- 既存ロジック（[apps/gmo_bot/infra/alerting/slack_notifier.py](../apps/gmo_bot/infra/alerting/slack_notifier.py) の `is_market_data_maintenance_result`）を保存側にも適用

---

## 4. 優先度と工数感

| ID | 内容 | 優先度 | 工数 | 期待効果 |
|---|---|:---:|:---:|---|
| A-1 | SHORT 停止 | **★★★** | 5分 | 月損失の約75%削減見込み |
| A-2 | FAILED 原因調査 | ★★ | 1日 | 観測性改善 |
| B-1 | ATR SL 拡大検証 | ★★★ | 2日 | SL率 74% → 50%以下を目標 |
| B-2 | 高値圏 LONG 除外 | ★★ | 2日 | 勝率 28% → 35%以上 |
| B-3 | 連敗ストッパー | ★ | 1日 | 連敗クラスタ損失の半減 |
| C-1 | backtest 突合 | ★★★ | 3日 | 戦略 vs 執行の切り分け |
| C-2 | SHORT 再設計 | ★ | 1週間 | A-1の長期解 |
| C-3 | 自動アラート | ★ | 2日 | 早期検知 |
| D-1 | balance snapshot | ★★ | 1日 | レポート精度向上 |
| D-2 | cumulative PnL | ★★ | 0.5日 | レポート精度向上 |
| D-3 | FAILED 保存抑制 | ★ | 0.5日 | コスト削減 |

---

## 5. 観測指標（再評価のため）

A-1 実施後、以下が改善傾向か日次でモニタ:

| 指標 | 現状 | 目標 (1ヶ月後) |
|---|---:|---:|
| Profit Factor | 0.67 | **>= 1.2** |
| 勝率 | 27.8% | **>= 38%** |
| Net PnL (月次) | -872 JPY | **>= 0** |
| SL ヒット率 | 74% | <= 55% |
| 最長連敗 | 8 | <= 5 |
| Sharpe (daily, ann.) | -3.33 | >= 0.5 |

毎週 `python -m apps.gmo_bot.reports` を実行して進捗を追跡する。

---

## 6. 関連ドキュメント・コード

- レポート機能設計: [docs/gmo_bot_performance_report.md](./gmo_bot_performance_report.md)
- バックテスト基盤: [docs/research_backtest_platform.md](./research_backtest_platform.md)
- バックテスト妥当性: [docs/research_backtest_validity.md](./research_backtest_validity.md)
- 戦略実装: [apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py)
- パラメータスウィープ: [research/scripts/analyze_gmo_15m_param_sweep.py](../research/scripts/analyze_gmo_15m_param_sweep.py)
- 損失レジーム分析: [research/scripts/analyze_gmo_15m_loss_regime.py](../research/scripts/analyze_gmo_15m_loss_regime.py)
