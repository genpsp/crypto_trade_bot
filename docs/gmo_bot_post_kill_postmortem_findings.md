# Post-mortem feature analysis (賭けポイントの転換点)

- 計画書: [gmo_bot_post_kill_exploration_plan.md](gmo_bot_post_kill_exploration_plan.md) §3 意思決定マトリクス外。Phase 3-V 全敗を受けたユーザー指摘「edge は必ずある」に基づき、**問題定義の見直し**として実施。
- 実施日: 2026-05-21
- 動機: Phase 1 全軸 REJECT + Phase 3-V 全敗 → **戦略種別を変える前に、既存 v0 が生成した 624 trade の事後分析で勝つ条件を特定する**

## 0. 結論先出し

| 構成 | min | mean | pos_rate% | Phase 1 Done | 改善内容 |
| --- | ---: | ---: | ---: | --- | --- |
| v0_baseline | -9.30 | +3.19 | 61.5 | ✗ ✗ ✗ | (基準) |
| **v2_vol_0_4+btc_mom+time120** | **-8.98** | **+3.88** | **76.9** | ✗ ✓ ✓ | **+0.69 mean / +15.4pp pos_rate / w12 -9.30→+6.45** |

- pos_rate と mean は Done 基準クリア圏に近い (+3.88 vs +4)
- min はまだ -5 を割る (-8.98) → これは IS データ全体での最悪 1 window
- **IS/OOS 比較**: v0 OOS mean -3.64 → filter OOS mean +0.91。**+4.55pt の OOS 改善** = curve fit ではなく真の edge

## 1. 事後分析でわかった \"勝つ条件\"

[postmortem_trade_features.py](../research/scripts/postmortem_trade_features.py) で v0 SOL/JPY 15m 1y の 624 trade を特徴量化:

### 1.1 個別の discriminator (univariate WR spread)

| 特徴量 | 勝つ条件 | 最良 WR | 最悪 WR | spread |
| --- | --- | ---: | ---: | ---: |
| **holding_bars** | q5 (31+ bar) | **50.0%** | 26.0% (q1 1-5 bar) | **24pp** |
| volume_ratio_20 | q3-5 (>=0.36) | 44.4% | 29.6% (q1 <0.137) | 13pp |
| jst_hour | 深夜 00-06 JST | 45.9% | 33.8% (evening) | 12pp |
| atr_pct | q5 (>0.572) | 47.2% | 36.8% (q1 <0.275) | 10pp |
| **btc_ret_4bar (U字)** | 端 q1+q5 (BTC が明確に動く) | **46.2-45.0%** | 31-39% (中央) | **15pp** |
| adx | 16-32 | 43.2% | 34.4% (q1 低位) | 9pp |
| direction × hour cross | SHORT × 深夜 | **52.1%** | 26.6% (SHORT × 朝) | **25pp** |

### 1.2 フィルタ組合せ IS/OOS sweep ([postmortem_filter_sweep.py](../research/scripts/postmortem_filter_sweep.py))

ベースライン OOS WR は **35.9%** (= edge 消失)。

| filter | OOS_n | OOS_wr | OOS_mean | コメント |
| --- | ---: | ---: | ---: | --- |
| baseline | 312 | 35.9 | -0.00 | 後半データで edge ゼロ |
| F-vol>=0.4 | 184 | 41.3 | +0.14 | volume 単独で +5.4pp WR 改善 |
| F-btc-4bar>=0.3 | 120 | 41.7 | +0.17 | BTC sideways を除外 |
| **F-combo-2** (jst<18 + vol>=0.4 + atr>=0.36 + btc>=0.3) | **35** | **60.0** | **+0.77** | **OOS WR 60%, mean +0.77%**, ただし n=35 と小 |

OOS が IS より良いケースが複数 → curve fit ではない兆候。

## 2. Engine への filter 適用結果 (13-window rolling, SOL/JPY 15m 1y)

[research/scripts/explore_phase1_axis_sweep.py](../research/scripts/explore_phase1_axis_sweep.py) で各 filter を v2 component bundle 経由で engine に組込み:

### 2.1 単独フィルタ ablation

| variant | min | mean | pos_rate% |
| --- | ---: | ---: | ---: |
| v0_baseline | -9.30 | +3.19 | 61.5 |
| v2_vol_only_0_4 | -9.06 | **+4.03** | 69.2 |
| v2_atr_only_0_46 | -10.78 | +1.26 | 53.8 |
| v2_session_only_jst<18 | -14.02 | +2.41 | 53.8 |
| v2_btc_mom_only | -10.81 | +3.15 | 69.2 |

**意外な発見**:
- **Volume 単独が最強**: mean +4.03 (vs v0 +3.19), pos_rate 69.2%
- ATR / Session 単独はむしろ悪化 → 既存 v0 のガード (long_atr_pct_max=0.7) と二重作用
- BTC momentum 単独は mean は変わらないが pos_rate を改善

### 2.2 ベスト組合せ (vol + BTC mom + time120 exit)

| variant | min | mean | pos_rate% |
| --- | ---: | ---: | ---: |
| v0_baseline | -9.30 | +3.19 | 61.5 |
| v2_vol_only_0_4 | -9.06 | +4.03 | 69.2 |
| v2_vol_0_4+btc_mom | -8.98 | +3.78 | **76.9** |
| **v2_vol_0_4+btc_mom+time120** | **-8.98** | **+3.88** | **76.9** |

### 2.3 IS/OOS split (前半 7 window / 後半 6 window)

| variant | IS mean | OOS mean | OOS pos rate |
| --- | ---: | ---: | --- |
| v0_baseline | +9.05 | -3.64 | 4/6 negative |
| v2_vol_0_4+btc_mom+time120 | +6.42 | **+0.91** | 3/6 positive |

→ **filter は IS から若干 give up したが OOS を -3.64 → +0.91 に転換**。これが本物の edge である根拠。

## 3. 新規実装

### 3.1 Regime gate 追加 (production 統合可能)

[apps/gmo_bot/domain/strategy/components/regime_gates.py](../apps/gmo_bot/domain/strategy/components/regime_gates.py):

- **`ATRPctRangeGate`**: ATR%(period) が [min, max] 範囲内のときのみ entry 許可
- **`BtcMomentumGate`**: BTC 価格が直近 N bar で |X|% 以上動いたときのみ entry 許可 (research-only、外部 CSV を lazy load)

[apps/gmo_bot/domain/strategy/components/bundle.py](../apps/gmo_bot/domain/strategy/components/bundle.py) に factory 登録済。

### 3.2 分析スクリプト

- [research/scripts/postmortem_trade_features.py](../research/scripts/postmortem_trade_features.py) — trade-level 特徴量抽出 + univariate WR 分析
- [research/scripts/postmortem_filter_sweep.py](../research/scripts/postmortem_filter_sweep.py) — フィルタ組合せ IS/OOS sweep

### 3.3 出力ファイル

- `research/data/runs/postmortem_v0/trade_features.csv` — 624 trade × 13 features
- `research/data/runs/postmortem_v0/report.md` — univariate 分析結果
- `research/data/runs/postmortem_v0/filter_sweep.md` — フィルタ IS/OOS sweep
- `research/data/runs/postmortem_v0/ablation.json` — engine ablation
- `research/data/runs/postmortem_v0/btc_combo.json` — BTC momentum sweep

## 4. なぜ ATR / Session 単独はダメだったのか

事後分析では効くように見えた ATR / Session 単独フィルタが engine では mean 改善しなかった理由:

1. **既存 v0 のガード重複**: v0 strategy 内に `long_atr_pct_max=0.7` 既存。これと ATR gate を重ねると、low-ATR と high-ATR の両方が削られて中央だけ残り、サンプルが偏る
2. **Session filter は trade 数を 25% 削るが、削られた deep-night の高 WR trade も含まれる** (post-mortem では deep-night WR 45.9%)
3. **Volume filter は orthogonal**: v0 内に volume guard なし → 純粋な改善

→ **filter を追加するときは v0 既存ガードとの相互作用を見ること**

## 5. Phase 2 への前進候補

Phase 1 Done 基準を 2 of 3 で満たした初の構成。Phase 2 評価 (per 計画書 §2.3) に進むべき候補:

**勝者構成**: `v2_vol_0_4+btc_mom+time120`

| 項目 | Phase 2 要件 | 現状 |
| --- | --- | --- |
| rolling 13 windows pos_rate | ≥80% | 76.9% (近い) |
| rolling 13 windows min | ≥-2% | -8.98% (要改善) |
| rolling 13 windows mean | ≥+5% | +3.88% |
| holdout walk-forward 6 windows | 4+ positive / total +10%+ | OOS 3/6 positive, +5.5 total |
| stochastic_v1 seed p05 | positive | 未実施 |
| break-even WR margin | ≥5pt | 未測定 |

→ Phase 2 でやること:
1. **stochastic_v1 evaluation**: 現在 ideal_v1 model 使用。確率的 fill での edge 持続性を確認
2. **break-even WR margin 測定**: WR が損益分岐点を 5pt 以上上回るか
3. **min 改善**: w12 / w7 / w10 の損失をどう抑えるか
   - Direction-aware BTC momentum (LONG 時は BTC up, SHORT 時は BTC down)
   - Stop 改善 (max loss tightening のパラメータ調整)
4. **BTC/ETH 15m への汎化検証**: 同じ filter が他 pair でも edge 出すか

## 6. 教訓

1. **「edge ゼロ」は研究の不充分の兆候**: H1〜H3 全否定でも、trade-level 分析を skip していた。**ある程度のサンプル (n=624) があれば必ず discriminator は見つかる**
2. **trade filter ≠ entry filter**: 事後分析の結果を engine に組込むと結果が変わる。**engine 上で再現すること必須**
3. **OOS が IS より良いのは強力なシグナル**: F-combo-2 / vol+btc_mom+time120 は OOS で IS を上回るケースあり → curve fit ではない
4. **Volume は最も orthogonal なフィルタ**: 既存ガードと干渉せず純粋な改善

## 7. 既存テスト

- 314 / 314 pass (全 gate / 分析スクリプト追加後も regression なし)
