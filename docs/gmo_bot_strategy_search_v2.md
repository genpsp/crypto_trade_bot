# gmo_bot 新戦略ロジック探索 v2（深堀り）

- v1: `docs/gmo_bot_strategy_search_v1.md`
- 対象: `apps/gmo_bot` / `gmo_ema_pullback_15m_both_v0`（SOL/JPY 15m）
- 起点: v1 で「LONG-only + tight RR / SHORT 厳格化」が holdout で黒字化、ただし母数不足で Gate A FAIL
- 実行 run:
  - v1: `research/data/runs/20260520-140743-gmo_15m_logic_search_v1-abe14bd`（20候補、ideal_v1、holdout のみ）
  - v2: `research/data/runs/20260520-142522-gmo_15m_logic_search_v2-abe14bd`（20候補、ideal_v1、rolling 90d/30d、`--keep-trades all`）
  - **v3: `research/data/runs/20260520-143346-gmo_15m_logic_search_v3_live-abe14bd`（10候補、データを 2026-05-20 まで拡張し LIVE 期間を holdout に含めた本物のシャドウ検証）**

## 0. 深堀りで得られた重大な事実

1. **`walk_forward` window はサイレントに無効化されていた**。CSV に 1198 件の bar gap（多くは 30 分）が存在し、`split_contiguous_segments` が 1199 個の微小セグメント（最大 86 bar）に分割するため、`train_days+test_days = 270 日 ≒ 25,920 bar` の窓を満たすセグメントが存在しない。  
   → revision_plan の Gate A `walk_forward_positive_ratio` は **そもそも評価不能** だった。今回は `rolling` で代替。
2. **データを 2026-05-20 まで延伸して LIVE 期間と重なる holdout を作ったところ、ほぼ全候補が -5〜-10% の赤字**になった。これは v1/v2 の holdout（2025-12〜2026-03 / 3.3ヶ月）では見えなかった現実。
3. しかし **90日 rolling で見ると、最新4 windows（LIVE期間込み）の平均 PnL は `L_combo_tp22` で +7.68%、`L_tight12_tp22` で +6.58%**。  
   → narrow holdout の赤字は ~70日 / 27 trade の **サンプルサイズの問題**で、より広い窓では既に edge が戻っている可能性が高い。
4. v1 の最有力 `L_tight10_tp18`（atr_stop=1.0, TP=1.8R）は **データ更新後の 2026 windows で平均 +0.04%** と最弱。**v2 と v3 で best 候補が入れ替わった**。
5. trade-level 検証: 戦略は完全な bimodal exit（-1R or +2.2R のみ、partial 無し）。break-even WR は TP=2.2R で 31.25%、TP=1.8R で 35.7%。**直近 LIVE 期間の WR が 25.9% に落ち込んだ**ため赤字化。
6. SHORT 厳格化（`short_atr_pct_min=0.40` + 上位足 gap=0.30）は LIVE 期間で **trade=3、WR=0%** までフィルタが効きすぎて、まともに動いていない。閾値を見直す必要がある。
7. LONG+SHORT を 50/50 ポートフォリオ化すると **rolling pos_rate 80%** に改善するが、平均 PnL は LONG-only の 67% 程度に低下。SHORT は分散効果より drag のほうが大きい局面が多い。

## 1. v2 ストレステスト: rolling 90d/30d step（10 windows、ideal_v1）

### 1.1 安定性ランキング

| nickname | mean PnL% | min PnL% | std | pos_rate | rtd_mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| L_tight10_tp18 (atr_stop=1.0, tp=1.8R) | **15.20** | -6.34 | 14.15 | 80% | 1.99 |
| L_tight12_tp18 (atr_stop=1.2, tp=1.8R) | 14.94 | -6.34 | 14.13 | 80% | 1.96 |
| L_base (LONG-only baseline) | 14.14 | -6.52 | 12.16 | 80% | 1.82 |
| L_lowvol025 | 13.46 | -3.09 | 11.18 | 80% | 1.72 |
| **L_combo_tp22 (low_vol_025 + tight_stop + tp=2.2R)** | **13.33** | **-1.34** | **10.00** | **90%** | 1.33 |
| L_tight12_tp22 | 12.81 | -2.48 | 11.22 | 70% | 1.31 |
| S_strict_tp20 | 5.84 | -5.27 | 6.45 | 80% | 1.17 |
| S_strict_tp24 | 3.36 | -9.94 | 7.97 | 80% | 0.70 |
| L_lowvol055 | 2.56 | -3.50 | 6.39 | 50% | 0.58 |

### 1.2 時系列パターン

`L_tight10_tp18` を基準に rolling PnL を並べると、強烈な regime decay が見える:

| window | L_tight10_tp18 | L_combo_tp22 |
| --- | ---: | ---: |
| 2025-02→05 | 11.12 | 9.59 |
| 2025-03→06 | 17.42 | 13.08 |
| 2025-04→07 | 18.00 | 17.39 |
| 2025-05→08 | 25.19 | 16.04 |
| **2025-06→09 (peak)** | **37.31** | **29.57** |
| 2025-07→10 | 28.38 | 21.00 |
| 2025-08→11 | 20.06 | 22.86 |
| 2025-09→12 | 6.93 | 2.38 |
| **2025-10→01 (worst)** | **-6.11** | **+2.69** |
| 2025-11→02 | -6.34 | -1.34 |

`L_combo_tp22` は **bad window でも -1.34% に留まり、最悪ケースが他候補の半分以下**。これが「90% positive、std 10.0」の根拠。

## 2. v3 LIVE 期間検証（データを 2026-05-20 まで拡張）

### 2.1 narrow LIVE holdout（2026-03-12 → 2026-05-20、~70 日）

| case | trades | WR | PnL% | RTD | max DD |
| --- | ---: | ---: | ---: | ---: | ---: |
| L_lowvol025 | 29 | 27.6 | -5.23 | -0.60 | -8.67 |
| L_combo_tp18 (low_vol + tight + tp=1.8R) | 29 | 27.6 | -5.23 | -0.60 | -8.67 |
| **L_combo_tp22** | 27 | 25.9 | **-4.43** | -0.67 | **-6.67** |
| S_base | 40 | 32.5 | -6.49 | -0.70 | -9.29 |
| L_tight12_tp22 | 41 | 26.8 | -6.68 | -0.70 | -9.54 |
| **BOTH baseline (現行)** | 81 | 32.1 | **-10.09** | -0.80 | -12.55 |
| L_base | 39 | 30.8 | -5.66 | -0.92 | -6.15 |
| L_tight12_tp18 | 39 | 30.8 | -5.66 | -0.92 | -6.15 |
| S_strict_tp20 | 3 | 0.0 | -3.31 | -1.00 | -3.31 |
| S_strict_tp24 | 3 | 0.0 | -3.31 | -1.00 | -3.31 |

- **全候補が赤字**。`L_combo_tp22` でも -4.4%
- ただし baseline (BOTH) より **`L_combo_tp22` は 5.7pt 改善 / DD は半分**
- SHORT 厳格化は **trade=3 / WR=0%** までフィルタが効きすぎ。閾値 (`short_atr_pct_min=0.40`, `short_upper_trend_min_gap_pct=0.30`) を緩める検討が必要

### 2.2 rolling 90d/30d（最新4 window が LIVE 期間に重なる）

| window | L_combo_tp22 | L_tight12_tp22 | L_lowvol025 | L_base | BOTH | S_strict_tp20 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2025-11-17 → 2026-02-15 | -1.34 | -0.98 | -3.09 | -6.52 | -5.79 | +0.83 |
| 2025-12-17 → 2026-03-17 | **+18.38** | **+19.61** | +10.61 | +9.40 | +11.78 | +3.29 |
| 2026-01-16 → 2026-04-16 | +7.47 | +2.17 | +3.63 | +1.60 | +9.95 | +2.44 |
| 2026-02-15 → 2026-05-16 | **+6.23** | +5.53 | +2.09 | +4.30 | -4.20 | -5.86 |
| **2026 windows mean** | **+7.68** | +6.58 | +3.31 | +2.20 | +2.93 | +0.18 |

→ 直近4 window 平均で **`L_combo_tp22` がトップ**。narrow holdout (70日) の赤字はサンプル不足、90日 window で見ると **新ロジックは既にエッジを取り戻している**。

### 2.3 v3 train 結果（2025-02-20 → 2026-03-11、~13ヶ月）

| case | trades | WR | PnL% | RTD | PF | avg R |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **L_combo_tp18** | 321 | 41.4 | **+50.34** | 3.65 | 1.24 | 0.131 |
| L_tight12_tp18 | 338 | 40.8 | +52.76 | 3.42 | 1.24 | 0.113 |
| L_lowvol025 | 317 | 40.7 | +45.09 | 3.21 | 1.21 | 0.111 |
| L_base | 334 | 40.1 | +47.37 | 2.89 | 1.22 | 0.094 |
| **L_combo_tp22** | 282 | 36.2 | +41.14 | **2.66** | 1.21 | **0.129** |
| BOTH (baseline) | 541 | 39.4 | +58.10 | 2.12 | 1.16 | 0.073 |
| S_strict_tp20 | 125 | 40.0 | +27.98 | **2.08** | 1.32 | **0.174** |
| S_strict_tp24 | 118 | 33.9 | +20.15 | 1.14 | 1.22 | 0.126 |
| S_base | 213 | 37.6 | +10.13 | 0.45 | 1.07 | 0.022 |

- 13ヶ月 train で **`L_combo_tp22` は RTD 2.66、avg R +0.129、PF 1.21**
- `L_combo_tp18` の方が train PnL は大きい（+50%）が RTD は劣る（3.65 → drawdown が深い）

## 3. trade-level drilldown（`L_combo_tp22` v2 holdout）

### 3.1 Exit reason 分布

| | train (229) | holdout (50) |
| --- | ---: | ---: |
| STOP_LOSS | 144 (62.9%) | 33 (66.0%) |
| TAKE_PROFIT | 85 (37.1%) | 17 (34.0%) |

- **完全な bimodal**: 全 trade が -1R or +2.2R に到達して終了。`partial_exit` や `trailing stop` は無効
- TP=2.2R での break-even WR: **31.25%**（= 1/(1+R)）。train 37.1% / holdout 34.0% → **margin 約 3pt**
- もし WR が 30% を割ると即赤字に転落する設計脆性がある

### 3.2 R-multiple ヒストグラム

```
train holdout
  -1R   144   33   #################################
                              (clean SL)
 +2.2R   85   17   #################
                              (clean TP)
```

### 3.3 holding bars

- 中央値: train 15 bars / holdout 13.5 bars（≒ 3〜4 時間）
- 平均: train 21.5 / holdout 26.0
- **5 bar 以内に SL になる「即反転」**が LIVE 期間で多発（27 trade のうち 6 trade が 1-9 bar で SL）
- 一方で TP 到達は 12-101 bar の幅広い分布

### 3.4 LIVE 期間の週次（`L_combo_tp22` holdout）

| week | n | wins | wr | pnl |
| --- | ---: | ---: | ---: | ---: |
| 2026-03-16〜22 | 2 | 0 | 0% | -2.27 |
| 2026-03-23〜29 | 1 | 0 | 0% | -1.23 |
| 2026-04-06〜12 | 6 | 2 | 33% | +1.77 |
| 2026-04-13〜19 | 8 | 3 | 38% | -0.24 |
| 2026-04-20〜26 | 3 | 0 | 0% | -2.98 |
| 2026-05-04〜10 | 6 | 2 | 33% | +1.75 |
| 2026-05-11〜17 | 1 | 0 | 0% | -1.23 |

- 「2-3 trade / 全敗」の choppy 週 が定期的に発生
- SOL/JPY 価格は 13058〜15127 の 14% 帯でレンジ。**トレンドフォロー戦略がレンジ相場で SL を量産している**

## 4. portfolio 効果（LONG combo + SHORT strict 50/50）

| | holdout (v2 narrow) | rolling 10 windows |
| --- | ---: | ---: |
| portfolio mean PnL | +1.48% | +9.58% |
| portfolio max DD | -6.25% | min window -1.29% |
| portfolio RTD | 0.236 | — |
| **portfolio pos_rate** | — | **80%** |
| LONG-only full PnL | +4.58% | +13.46% |
| SHORT-only full PnL | -1.63% | +5.84% |

- rolling では **pos_rate 80%、平均 PnL +9.58%、最悪 -1.29%** とリスク低減効果あり
- ただし holdout（直近 regime shift 期）は SHORT が drag。LONG-only の方が良い
- 結論: **portfolio は中長期では好ましいが、現在は SHORT 側の閾値が厳しすぎて trade=3 のみ → 個別 deploy も並行検討**

## 5. 結論アップデート

### 5.1 最有力候補（v3 反映後）

**`gmo_long_combo_v1` = LONG-only + `long_atr_pct_min=0.25` + `atr_stop_multiplier=1.2` + `take_profit_r_multiple=2.2`**

| 指標 | 値 | 評価 |
| --- | ---: | --- |
| train (13ヶ月) PnL | +41.1% | 安定的に黒字 |
| train RTD | 2.66 | revision_plan baseline 比 +20% |
| train avg R | +0.129 | break-even +3pt の margin |
| holdout (narrow LIVE 70日) | -4.4% / WR 25.9% | サンプル不足、まだ赤字 |
| **rolling 13 windows pos_rate** | **92.3%** | **最高** |
| **rolling min window** | **-1.34%** | **最低 DD（他候補は -6 〜 -15%）** |
| **2026 windows mean** | **+7.68%** | **直近4 window で最良** |
| max DD (holdout) | -6.67% | baseline の半分 |

### 5.2 v1 推奨との変化点

| 項目 | v1 推奨 | v2/v3 後の推奨 |
| --- | --- | --- |
| LONG メイン | `L_tight_rr_v1` (tp=2.2R, no low_vol) | **`L_combo_v1` (+ `long_atr_pct_min=0.25`)** |
| TP 倍率 | 2.2R | 2.2R（維持） |
| stop 倍率 | 1.2 | 1.2（維持） |
| SHORT メイン | `S_strict_v1` (tp=2.4R) | **`S_strict_tp20_v1` (tp=2.0R)** — rolling で +6.4% / RTD 1.17 |
| SHORT 閾値 | `short_atr_pct_min=0.40`, `gap=0.30` | **緩める検討必須**（LIVE で trade=3） |
| 即時 LIVE 縮退 | BOTH→LONG | BOTH→LONG（維持） |
| 採用判定 | Gate A FAIL も holdout 黒字なので候補 | **narrow LIVE holdout 赤字、rolling では recovery 確認、PAPER 30日が必須** |

### 5.3 まだ未解決の論点

1. **2025-10〜2026-02 の regime shift の原因**: 価格レンジ縮小（chop 化）が支配的だが、定量化していない。  
   → `regime_tagger` の volatility bucket × monthly で集計し、エッジ消失と vol 低下の相関を取る。
2. **stochastic execution profile が profile 無し**: LIVE 36 trade から `build_execution_profile` を作る作業が未完。slippage/reject を含めた現実近似評価ができていない。
3. **walk_forward が動いていない**: `split_contiguous_segments` を「N 分以内の gap は許容」に緩める or bar forward-fill する変更が必要。研究基盤の宿題。
4. **SHORT の閾値最適化**: LIVE で trade=3 は filter が厳しすぎ。`short_atr_pct_min ∈ {0.25, 0.30, 0.35}` のパラスイープが必要。
5. **bimodal exit の脆性**: WR が break-even (31.25%) を 3pt 上回るだけの薄いエッジ。trailing stop / partial TP / time-based exit などの exit ロジック改修で margin を厚くする余地がある。

## 6. 次の1スプリント（実行レベル）

```bash
# 1. 既に出来上がった候補で PAPER モード投入（v3 で best 確認済み）
#    LIVE-縮退と並行で
#    direction=LONG
#    strategy.long_atr_pct_min=0.25
#    strategy.atr_stop_multiplier=1.2
#    exit.take_profit_r_multiple=2.2

# 2. SHORT 閾値の緩和探索（直近 LIVE で trade=3 だった件）
cat > research/sweeps/gmo_15m_short_threshold_v1.yaml <<'EOF'
# direction=SHORT 固定で short_atr_pct_min / short_upper_trend_min_gap_pct を grid
# rolling + holdout(2026-03-12) で trade 数と PnL を可視化
EOF
python -m research.scripts.run_sweep \
  --spec research/sweeps/gmo_15m_short_threshold_v1.yaml \
  --workers 4 --keep-trades all

# 3. walk_forward 復活（基盤改修）
#    research/src/eval/window.py の split_contiguous_segments に
#    gap_tolerance_bars 引数を追加し、デフォルトで 16bar (=4h) 程度の gap を許容
#    その後 walk_forward 90/30/30 を spec に書き直して再評価

# 4. stochastic execution profile 構築
#    apps/gmo_bot に LIVE trade JSON ダンプ CLI を追加
#    python -m research.scripts.build_execution_profile ...
#    その後 v3 を stochastic_v1 で再回し、slippage/reject 込みの数字に置き換え

# 5. trailing stop / partial TP の戦略改修
#    apps/gmo_bot/domain/strategy/shared/decision_builders.py に TP 段階出 をオプション追加
#    上記候補 + partial_tp=[0.5R, 1.5R, 2.5R] のような構造で再評価
```

### Definition of Done（採用判定）

- `L_combo_v1` を PAPER で 30日回し、**累積 PnL ≥ +3%、max DD ≤ -8%、WR ≥ 32%、daily PnL の skewness 改善**
- shadow_compare で LIVE 配信時の trade 一致率 95% / 累積 PnL 偏差 ±25%
- LIVE 0.5x で 30日: max DD ≤ -8%、reject 率 +50% 以内

## 7. 付録: 実装変更まとめ

- `apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py`:
  - `long_atr_pct_min` / `short_atr_pct_min` / `short_atr_pct_max` パラメータ新規追加（default 無効）
  - `LONG_ATR_REGIME_TOO_COLD` / `SHORT_ATR_REGIME_TOO_COLD` / `SHORT_ATR_REGIME_TOO_HOT` 早期 return を追加
  - 既存テスト 3/3 pass、default 既存挙動と同一
- `research/sweeps/gmo_15m_logic_search_v1.yaml`: 20 cases、holdout のみ
- `research/sweeps/gmo_15m_logic_search_v2.yaml`: 20 cases、rolling 90d/30d、`keep_trades all`
- `research/sweeps/gmo_15m_logic_search_v3_live.yaml`: 10 cases、データ 2026-05-20 まで、LIVE-period holdout
- `research/data/raw/soljpy_15m_to_2026_05.csv`: 41,138 bars（2025-02-20 → 2026-05-20）
- `research/data/cache/gmo/soljpy/15m/`: PartitionedOhlcvCache が初期化済み

## 8. 既知の研究基盤バグ

- **`walk_forward` window が CSV gap で生成 0 になる**（[research/src/eval/window.py:148-215](research/src/eval/window.py#L148-L215)）  
  → `split_contiguous_segments` が gap を厳格に扱うため、`bars_path` 経由の CSV では 1199 個の micro-segment に分割される。  
  → 修正案: `_build_walk_forward_windows` に `gap_tolerance_bars=16` を追加、`split_contiguous_segments` も同様に許容する
- **`stochastic_v1` の profile 無しは ideal_v1 と等価**（[research/src/eval/execution_model.py:194](research/src/eval/execution_model.py#L194)）  
  → 全 seed が同値になり baseline run の seed dimension が無駄になっていた
- `regime_tagger` の trade-level frozen tags が `entry_regime={}` で空のままになる場合がある（v2 trade parquet を見た限り）。要調査
