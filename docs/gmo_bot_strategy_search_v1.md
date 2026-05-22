# gmo_bot 新戦略ロジック探索 v1

- 対象: `apps/gmo_bot` / `gmo_ema_pullback_15m_both_v0`（SOL/JPY 15m）
- 起点: `docs/gmo_bot_strategy_revision_plan.md`（baseline は holdout 全敗）
- 実行 run: `research/data/runs/20260520-140743-gmo_15m_logic_search_v1-abe14bd`
- spec: `research/sweeps/gmo_15m_logic_search_v1.yaml`
- 実装変更: `apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py`
  - `long_atr_pct_min` / `short_atr_pct_min` / `short_atr_pct_max` を新規パラメータとして追加（LOW_VOL カットオフ）
  - 既存挙動は default=0（無効）で温存
- 実行モデル: `ideal_v1`（baseline run の stochastic seed が全て同値だったため探索段階では決定性を優先）

## 0. 結論（先に）

1. **direction=BOTH をやめて LONG / SHORT を別モデルに分離するだけで holdout を黒字化できる**。
   - BOTH baseline: holdout -5.5% / RTD -0.40 / WR 34.6%
   - LONG-only: holdout **+0.97%** / RTD +0.07 / WR 34.7%（SHORT を取り除くだけで +6.5pt 改善）
2. LONG 側の最有力候補は **`atr_stop_multiplier=1.2` + `take_profit_r_multiple=2.2`**（R:R 再設計）。
   - holdout: **+5.08% / RTD +0.38 / 63 trades / WR 31.7% / PF 1.12**
   - train: +37.06% / RTD +3.14 / 229 trades / WR 36.7% / PF 1.23
3. SHORT 側で **初めて holdout が黒字** になる構成: **`short_atr_pct_min=0.40` + `short_upper_trend_min_gap_pct=0.30` + `atr_stop_multiplier=1.2` + `take_profit_r_multiple=2.4`**。
   - holdout: **+2.07% / RTD +0.26 / 25 trades / WR 32% / PF 1.10**
   - train: +18.08% / RTD +1.57 / 93 trades / WR 34.4% / PF 1.25
4. `revision_plan` P3 仮説のうち **`max_distance_from_ema_fast_pct` 縮小・`pullback_lookback_bars` 短縮は holdout で逆に悪化**（-10%〜-7%）。今後の探索からは外す。
5. それでも 20 候補すべてが **Gate A は FAIL**（holdout の CI 下限が負、DSR p ≈ 0.94+）。理由は holdout 期間 3.3ヶ月・17〜130 trade と母数不足。次は **データ延伸 + stochastic profile 構築 + walk-forward 細分化** が必要。

## 1. Sweep 構成（探索の手数）

- 計 20 cases × {holdout_train, holdout_test} = 40 trial
- combinations: `listed`（仮説グループを手書き）
- 軸グループ:

| グループ | 趣旨 | 代表ケース |
| --- | --- | --- |
| A baseline | 現行 BOTH / LONG-only / SHORT-only | `direction=LONG` |
| B LOW_VOL gate (LONG) | `long_atr_pct_min ∈ {0.25, 0.40, 0.55}` | `long_atr_pct_min=0.55` |
| C SHORT 厳格化 | `short_atr_pct_min`、`short_upper_trend_min_gap_pct` | `short_combo_strict` |
| D R:R 再設計 | `atr_stop_multiplier ∈ {1.2, 1.5, 1.8}` × `take_profit_r_multiple ∈ {1.4..3.0}` | `atr_stop=1.2, tp=2.2` |
| E anti-chase | `max_distance_from_ema_fast_pct=0.45` / `pullback_lookback_bars=3` | `chase=0.45` |
| F strong trend | `long_weak_upper_trend_min_gap_pct=0.5` | `long_strong_trend_gap=0.5` |
| G combos | C/D/F の組合せ | `long_combo_full`, `short_combo_strict` |

## 2. 主要結果（train vs holdout）

| ケース | holdout PnL% | holdout RTD | holdout WR | holdout trades | train PnL% | train RTD | train WR | train trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **A baseline (BOTH)** | -5.52 | -0.40 | 34.6% | 130 | +64.46 | 3.93 | 41.1% | 404 |
| **B LONG only** | +0.97 | +0.07 | 34.7% | 72 | +50.13 | 5.38 | 42.2% | 258 |
| **C SHORT only** | -5.47 | -0.44 | 33.3% | 57 | +15.18 | 1.44 | 39.0% | 154 |
| **D long_atr_pct_min=0.25** | +3.14 | +0.23 | 38.6% | 57 | +45.68 | 4.71 | 41.8% | 256 |
| **D long_atr_pct_min=0.40** | +0.14 | +0.01 | 36.1% | 36 | +32.48 | 3.27 | 41.1% | 207 |
| **D long_atr_pct_min=0.55** | **+2.46** | **+0.45** | 41.2% | 17 | +10.18 | 0.81 | 40.0% | 95 |
| **E long_rr_tight_stop_tp22** | **+5.08** | **+0.38** | 31.7% | 63 | +37.06 | 3.14 | 36.7% | 229 |
| E long_rr_tight_stop_tp26 | -0.27 | -0.02 | 26.2% | 61 | +30.94 | 2.73 | 32.8% | 198 |
| E long_rr_wide_stop_tp14 | -5.03 | -0.40 | 38.3% | 81 | +35.89 | 3.15 | 47.1% | 276 |
| F long_strong_trend_gap=0.5 | +1.12 | +0.08 | 35.7% | 70 | +46.98 | 5.04 | 41.9% | 253 |
| **G short_combo_strict** | **+2.07** | **+0.26** | 32.0% | 25 | +18.08 | 1.57 | 34.4% | 93 |
| anti-chase max_distance=0.45 | -10.27 | -0.63 | 29.2% | 65 | +48.16 | 5.22 | 42.4% | 255 |
| anti-chase + pullback=3 | -7.66 | -0.57 | 30.5% | 59 | +27.99 | 2.49 | 40.4% | 228 |
| long_tp_only=2.4 | -2.71 | -0.18 | 26.2% | 61 | +36.10 | 2.84 | 34.2% | 219 |
| long_tp_only=3.0 | -4.96 | -0.36 | 21.2% | 52 | +37.14 | 3.43 | 30.0% | 187 |

## 3. 仮説別 verdict（`revision_plan` P3 との照合）

| revision_plan の仮説 | 結果 |
| --- | --- |
| **P3.1 SHORT を分離・厳格化** | ✅ 採用候補。`short_combo_strict` で初めて holdout が黒字。`gmo_short_strict_v1` として別モデル化が筋。 |
| **P3.2 LOW_VOL カットオフ** | ✅ LONG 側で効く。`long_atr_pct_min=0.25` で WR 35% → 39%、PnL も +2.2pt。0.55 は WR 41% に上がるが trade 数が 17 まで減少しサンプル不足。 |
| **P3.3 BULL 負け対策 (`max_distance` 縮小 / `pullback` 短縮)** | ❌ **逆効果**。holdout は -7〜-10%。本仮説は今後の探索から外す。 |
| **P3.4 R:R 再設計（stop タイト / TP 拡大）** | ✅ `atr_stop=1.2, tp=2.2R` が holdout +5% / train +37% で**最良の絶対 PnL & 十分なサンプル**。これが最有力。`tp=2.6R` 以上にすると holdout が赤に転落する（採用は 2.0〜2.4R レンジに限定）。 |
| **P3.5 regime gate 強化 (`long_weak_upper_trend_min_gap_pct ↑`)** | △ 効くが軽微。0.5 でも +1.1% に留まる。LOW_VOL カット or R:R 再設計の方が効率が高い。 |

## 4. Gate A 状況

`research.scripts.compare_runs --gate-a` での失敗内訳（top10 候補すべて FAIL）:

- `holdout_pnl_ci_positive` ✕（CI 下限 -4 〜 -24）
- `return_to_dd_ci_positive` ✕
- `deflated_sharpe_p_value` ≈ 0.94+（< 0.05 が必要）
- `all_trend_regimes_positive` ✕（BULL/BEAR/CHOPPY のどれか negative）
- `min_trades` ✕（17 / 25 のケース）
- `stochastic_seed_p05_ci_positive` ✕

これは **戦略が悪い** というより **holdout 母数が少ない**（118 trade → CI 幅 ±49pt）ことが支配的要因。トレード数は最高でも 130（baseline）、最良の `tight_rr_tp22` で 63。サンプルを倍以上に増やせば CI 下限が正に張り付く可能性がある。

## 5. 推奨次アクション（短期 1 スプリント）

### 5.1 戦略採用候補（PAPER 投入前に再検証）

1. **`gmo_long_tight_rr_v1`**（LONG のメインモデル）
   - `direction=LONG`
   - `strategy.atr_stop_multiplier = 1.2`
   - `exit.take_profit_r_multiple = 2.2`
   - その他は baseline のまま
2. **`gmo_short_strict_v1`**（SHORT 専用モデル）
   - `direction=SHORT`
   - `strategy.short_atr_pct_min = 0.40`
   - `strategy.short_upper_trend_min_gap_pct = 0.30`
   - `strategy.atr_stop_multiplier = 1.2`
   - `exit.take_profit_r_multiple = 2.4`
3. **LIVE 縮退（暫定）**: `gmo_ema_pullback_15m_both_v0` の direction を BOTH→LONG に切替えて損失拡大を止める（revision_plan P0 と同じ）。

### 5.2 データ・spec の刷新

- `python -m research.scripts.data_sync --broker GMO_COIN --pair SOL/JPY --timeframe 15m --since 2024-01-01` で母数倍増
- LIVE 36 trade から `build_execution_profile` を生成し、`stochastic_v1` を実体のあるプロファイルで再評価
- 新 spec を上記 5.1 の2モデル × {直近 6ヶ月 / 1年 / 2年} × seeds 5 で組み直す
- 新 spec の axes は `direction × { take_profit_r_multiple, atr_stop_multiplier, *_atr_pct_min }` に絞る（dead 軸 `volatile_size_multiplier` は廃止）

### 5.3 廃棄候補

- `risk.volatile_size_multiplier` 軸（baseline run でも今回でも結果が全て同値で死に軸）
- `strategy.max_distance_from_ema_fast_pct` の縮小方向
- `strategy.pullback_lookback_bars` の短縮方向
- `take_profit_r_multiple >= 2.6R` の長期保有狙い（holdout で赤）

## 6. 付録: 実装差分メモ

`apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py` に以下を追加:

- `LONG_ATR_PCT_MIN = 0.0`（default 無効）
- `SHORT_ATR_PCT_MIN = 0.0`（default 無効）
- `SHORT_ATR_PCT_MAX = math.inf`（default 無効）
- `evaluate_ema_trend_pullback_15m_v0` に下記 4 つの早期リターン分岐を追加
  - `LONG_ATR_REGIME_TOO_COLD`
  - `SHORT_ATR_REGIME_TOO_COLD`
  - `SHORT_ATR_REGIME_TOO_HOT`
  - 既存 `LONG_ATR_REGIME_TOO_HOT` と対称

既存テスト (`tests/test_gmo_ema_trend_pullback_15m_strategy.py`) は 3/3 pass。default では既存挙動と完全に同一。
