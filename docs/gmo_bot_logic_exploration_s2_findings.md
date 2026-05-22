# S2 Track A 探索結果（exit policy 単体評価）

- 対象: `apps/gmo_bot` SOL/JPY 15m / v2 component bundle
- データ: `research/data/raw/soljpy_15m_to_2026_05.csv`
- 実行モデル: `ideal_v1`（決定的、stochastic profile が無いため）
- 期間: 直近 30,000 bar (= 約 10 ヶ月)
- 評価スクリプト: [research/scripts/explore_track_a_exit_policies.py](../research/scripts/explore_track_a_exit_policies.py), [research/scripts/explore_track_a_rolling.py](../research/scripts/explore_track_a_rolling.py)

## 1. 単一期間（30,000 bar 連続）

`sum_scaled_pnl_pct` = portfolio multiplier の累積変化（実 PnL に相当）。

| case | closed | wins | WR% | sum_pnl%（unscaled） | sum_scaled% | mean_R |
| --- | --- | --- | --- | --- | --- | --- |
| **v0_baseline** | 466 | 180 | 38.63 | +35.06 | **+35.06** | +0.05 |
| v2_default_bundle | 466 | 180 | 38.63 | +35.06 | +35.06 | +0.05 |
| A1_BE_1_0R | 446 | 126 | 28.25 | +5.88 | +5.88 | -0.51 |
| A1_BE_0_8R | 440 | 111 | 25.23 | -4.08 | -4.08 | -0.47 |
| A1_BE_1_2R | 447 | 137 | 30.65 | +12.71 | +12.71 | -0.54 |
| A3_Chandelier_ATR_2_0 | 318 | 115 | 36.16 | -26.70 | -26.70 | -0.74 |
| A3_Chandelier_ATR_2_5 | 349 | 137 | 39.26 | -21.40 | -21.40 | -0.67 |
| A3_Chandelier_ATR_3_0 | 379 | 138 | 36.41 | -20.15 | -20.15 | -0.73 |
| A4_Time_30bar | 563 | 229 | 40.67 | +24.68 | +24.68 | +0.02 |
| A4_Time_60bar | 491 | 194 | 39.51 | +28.95 | +28.95 | +0.04 |
| **A4_Time_120bar** | 467 | 182 | 38.97 | +38.17 | **+38.17** | +0.05 |
| A4_Time_120bar_BE_cap | 467 | 182 | 38.97 | +38.17 | +38.17 | +0.05 |
| A2_Partial50_at_1R | 695 | 409 | 58.85 | +278.00 | +17.27 | +0.36 |
| A2_Partial50_at_1R_plus_BE | 695 | 409 | 58.85 | +278.00 | +17.27 | +0.36 |
| A1_plus_A4_BE_then_TimeBE | 447 | 128 | 28.64 | +8.28 | +8.28 | -0.51 |

### 観察

1. **v0 と v2_default_bundle が完全一致** — S1 Done 基準を実データで実証（466 trade × 30,000 bar）
2. **A4 Time 120bar が唯一 v0 を上回る** — +38.17% vs +35.06% (+3.1pt)。緩い time-cap で「ダラダラ含み損→TP 到達せず SL」になる trade を一部救う
3. **A1 BE は全幅で悪化** — 建値移動が早すぎ、TP まで伸びる trade を半分以上殺している。WR が 38.6% → 25〜30% に落ちる
4. **A3 Chandelier は全 ATR multiple で大赤字** — trailing 距離が ATR では狭すぎ、TP=12〜52 (vs v0=180) と TP 到達がほぼ無い
5. **A2 Partial50 は WR 58.85% と劇的に上がるが、portfolio multiplier では悪化** — partial で 1R 利確する代わりに runner の 2R upside が半減、ネットで TP 利益が縮む（unscaled の +278% は partial カウントによる artifact）

注: `sum_pnl_pct` (unscaled) は trade 単位の `(exit-entry)/entry*100` を単純合計するため、partial trade で record 数が増えると過大評価される。**意思決定は `sum_scaled_pnl_pct` で行うこと**。

## 2. Rolling 10 windows × 3,000 bar（直近 ~10 ヶ月）

各 window ~31 日。`sum_scaled_pnl_pct` で並べた抜粋:

| case | min | mean | pos_rate% |
| --- | --- | --- | --- |
| v0_baseline | -9.30 | +2.23 | 50.0 |
| v2_default_bundle | -9.30 | +2.23 | 50.0 |
| A1_BE_1_0R | -9.75 | -0.37 | 40.0 |
| A1_BE_0_8R | -9.48 | -0.88 | 40.0 |
| A1_BE_1_2R | -8.88 | -0.65 | 40.0 |
| A3_Chandelier_ATR_2_0 | -6.54 | -2.49 | 20.0 |
| A3_Chandelier_ATR_2_5 | -6.46 | -2.00 | 20.0 |
| A3_Chandelier_ATR_3_0 | -9.48 | -2.05 | 30.0 |
| A4_Time_30bar | -9.73 | +1.62 | 40.0 |
| A4_Time_60bar | -8.15 | +2.09 | 50.0 |
| **A4_Time_120bar** | **-8.15** | **+2.54** | 50.0 |
| A4_Time_120bar_BE_cap | -8.15 | +2.54 | 50.0 |
| A2_Partial50_at_1R | -7.24 | +0.67 | 40.0 |
| A2_Partial50_at_1R_plus_BE | -7.24 | +0.67 | 40.0 |
| A1_plus_A4_BE_then_TimeBE | -9.75 | -0.13 | 40.0 |

### 観察

- A4_Time_120bar は **min -8.15** (v0 -9.30 から +1.15pt 改善) / **mean +2.54** (v0 +2.23 から +0.31pt 改善) / pos_rate 維持。3 指標すべて v0 を marginal に上回る唯一のケース
- A1/A3 は pos_rate がはっきり下がる（50% → 20-40%）— 改悪確定
- A2 Partial の min は -7.24 で v0 より良いが、pos_rate と mean は劣る — trade-off

## 3. Track A の Done 基準達成状況

| 基準 | 目標 | v0 baseline | 最有力候補 (A4_Time_120bar) | Pass? |
| --- | --- | --- | --- | --- |
| rolling min PnL ≥ 0% | ≥ 0 | -9.30 | -8.15 | ❌ |
| rolling pos_rate ≥ 90% | ≥ 90 | 50.0 | 50.0 | ❌ |
| rolling mean PnL ≥ v0 | ≥ +2.23 | +2.23 | +2.54 | ✅ marginally |

**結論**: Track A 単独では Done 基準には届かない。**A4 Time 120bar は marginal な改善のみで、決定打にならない**。

## 4. 計画書 §4 の Track A 撤退条件との照合

> Track A 全体 | A1〜A5 のどれも `L_combo_v1` の rolling min を 0 まで持ち上げられない → exit 改修では解けない問題、Track B/C へリソース移動

→ **撤退条件に該当**。A1/A2/A3/A4 のいずれも rolling min を 0 まで持ち上げられない。**Track B (regime gate) / Track C (trend detection) へリソース移動が妥当**。

ただし A4 の marginal 改善は無価値ではなく、**Track B/C 候補と組み合わせるベース exit policy として A4 Time 120bar を残す**のが合理。

## 5. 次の一手

1. **Track B (regime gate) に進む** — chop window で entry を止める / size を絞ることで rolling min を改善できるか
2. **Track C (trend detection) に進む** — EMA9/34 cross を Supertrend / Donchian / HMA に置換、そもそも entry の品質を上げる
3. **A4 Time 120bar は default exit として組み込む** — Track B/C の評価ベースに使う

## 6. S2 で実装/検証したもの

### コード
- [apps/gmo_bot/domain/strategy/components/exit_policies.py](../apps/gmo_bot/domain/strategy/components/exit_policies.py)
  - `BreakEvenExit` (A1) / `ChandelierTrailExit` (A3) / `TimeExit` (A4) / `PartialTpExit` (A2) / `CompositeExit`
- [research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py)
  - Per-bar ExitPolicy 呼び出し
  - `BreakEvenAction` / `TrailAction` / `CloseAction` / `PartialTpAction` ハンドラ
  - Partial close 会計: `quantity_sol` のみ減算、`base_notional` は initial 維持、`partial_pnl_accrued_usdc` を runner exit 時に合算
- [tests/test_gmo_components_v2_reproduction.py](../tests/test_gmo_components_v2_reproduction.py)
  - 4 件: default 再現 / BE / Time / Partial の動作確認

### スクリプト
- [research/scripts/explore_track_a_exit_policies.py](../research/scripts/explore_track_a_exit_policies.py) — 単一期間 13-case 比較
- [research/scripts/explore_track_a_rolling.py](../research/scripts/explore_track_a_rolling.py) — rolling N-window 評価

### 生データ
- [research/data/processed/track_a_exit_policy_smoke_v5.json](../research/data/processed/track_a_exit_policy_smoke_v5.json)
- [research/data/processed/track_a_rolling_w10_b3000.json](../research/data/processed/track_a_rolling_w10_b3000.json)
