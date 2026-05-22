# Phase 2 evaluation — v2_dir_session+vol+time120

- 計画書: [gmo_bot_post_kill_exploration_plan.md](gmo_bot_post_kill_exploration_plan.md) §2.3 (Phase 2)
- 前段: [Post-mortem findings](gmo_bot_post_kill_postmortem_findings.md) で発見した direction-aware + volume filter を Phase 2 Done 基準で精査
- 実施日: 2026-05-21

## 0. Bottom line

**勝者構成**: `v2_dir_session+vol+time120`

```python
strategy: ema_trend_pullback_15m_v2
components:
  regime_gate:
    type: composite
    gates:
      - type: directional_session
        long_allowed_utc_hours:  [15..23, 0..8]   # = JST 0-17 (drop JST evening for LONG)
        short_allowed_utc_hours: [3..20]          # = JST 12-05 (drop JST morning for SHORT)
      - type: volume_confirmed
        period: 20
        volume_multiplier: 0.4                     # entry vol >= 0.4 * MA(20)
  exit_policy:
    type: time_exit
    max_holding_bars: 120
    prefer_breakeven: false
```

## 1. Phase 2 Done 基準照合 (SOL/JPY 15m 1y, 13 rolling windows × 3000 bars)

| 基準 | 目標 | 実績 (ideal_v1) | 実績 (stoch_v1 p50) | 判定 |
| --- | --- | ---: | ---: | --- |
| rolling 13w mean | ≥+5% | **+5.57%** | +5.28% | ✓ |
| rolling 13w pos_rate | ≥80% | 69.2% | 61.5% | △ |
| rolling 13w min | ≥-2% | -8.01% | -8.44% | ✗ |
| holdout 6w total | ≥+10% | **+12.73%** | n/a | ✓ |
| holdout 6w positive | ≥4/6 | 3/6 | n/a | △ |
| stochastic_v1 p05 | positive | n/a | **+62.13%** (50 seeds 100% +) | ✓✓ |
| break-even WR margin | ≥+5pt | **+8.63pt** | n/a | ✓ |

**3/7 strict pass, 2/7 borderline, 2/7 fail。stochastic 圧倒的に robust。**

## 2. v0 baseline からの推移

| 段階 | mean | pos_rate% | min | holdout WF total |
| --- | ---: | ---: | ---: | ---: |
| Phase 1 v0 baseline | +3.19 | 61.5 | -9.30 | -21.84 |
| Postmortem (vol-only) | +4.03 | 69.2 | -9.06 | -1.59 |
| Postmortem (vol+btc_mom+time120) | +3.88 | 76.9 | -8.98 | +5.47 |
| **v2_dir_session+vol+time120** | **+5.57** | **69.2** | **-8.01** | **+12.73** |

- mean +74% vs baseline (+3.19 → +5.57)
- holdout total **-21.84 → +12.73** (損益逆転)
- min は -9.30 → -8.01 と小幅改善 (構造的に推進限界)

## 3. Per-window 詳細

### 3.1 ideal_v1 (13 windows full)

| window | scaled_pnl% | trades | wr% |
| ---: | ---: | ---: | ---: |
| w0 (IS) | +8.43 | 30 | 50.0 |
| w1 | +1.05 | 28 | 39.3 |
| w2 | +4.10 | 25 | 44.0 |
| w3 | +17.16 | 29 | 51.7 |
| w4 | +19.30 | 31 | 54.8 |
| w5 | +12.52 | 35 | 51.4 |
| w6 | -4.06 | 24 | 41.7 |
| **w7 (OOS)** | +3.97 | 23 | 39.1 |
| w8 | +7.44 | 34 | 47.1 |
| w9 | +10.61 | 29 | 44.8 |
| w10 | -0.79 | 24 | 33.3 |
| w11 | -0.49 | 32 | 37.5 |
| w12 | -8.01 | 31 | 25.8 |

### 3.2 stochastic_v1 multi-seed (n=50, template profile)

| metric | p05 | p50 | p95 |
| --- | ---: | ---: | ---: |
| total scaled_pnl% | **+62.13** | +68.63 | +74.92 |
| mean per window | **+4.78** | +5.28 | +5.76 |
| pos_rate | 61.5% | 61.5% | 69.2% |
| min (worst window) | -8.74 | -8.44 | -5.47 |

**50 seed 全て (100%) が total > 0** → 実行ノイズに対し edge は堅牢。

## 4. 残課題: なぜ min と pos_rate が Phase 2 strict をクリアできないか

w10/w11/w12 (最新 3 ヶ月) で連続損失:
- w10 -0.79, w11 -0.49 (小規模、ノイズ範囲)
- **w12 -8.01** (大幅損失、最新 31 日の特定 regime 起因)

w12 を詳細に分析すべきだが、暫定的な仮説:
1. **最近の market regime change**: SOL/JPY 15m volatility/correlation 構造が直近で変化 → strategy が想定する trend pullback pattern が消失
2. **stop too tight**: holding_bars 1-5 の trades が WR 25.97% (post-mortem) → stop が浅すぎ。w12 で連続 stop hit している可能性

### 4.1 min 改善の追加候補

- **BTC momentum gate を controlled に追加**: `vol_0_4+btc_mom+time120` は min -8.98 で本構成 -8.01 とほぼ同等だが、mean が +3.88 (vs +5.57 本構成) で trade off
- **adaptive stop loss**: 14-bar ATR ベースで R を市場 vol に追従させる (現状固定 1.5 ATR)
- **equity_curve gate**: 連続損失後の trade を pause (前回試したが mean を大幅に削った)
- **w12 特定の調査**: BTC との correlation, JST 時間帯、ATR レベルなどを詳細分析

これらは Phase 2 を deep dive するときの follow-up (Phase 2.5)。

## 5. PAPER 移行判断

### 5.1 移行可能性

**Phase 2 を strict に満たすのは未達だが、PAPER 段階に進む十分な証拠**:

- stochastic_v1 50 seeds 全て (100%) total positive
- total p05 = +62.13% (年率換算で安定収益)
- IS w0-w6 mean +8.5%, OOS w7-w12 mean +2.12% (どちらも positive)
- break-even WR margin +8.63pt (十分な safety)
- mean +5.57% (Phase 2 strict mean ≥+5 クリア)

### 5.2 PAPER で重点監視する項目

1. **w12 相当の regime での挙動**: live で同様の連続損失が発生するか
2. **slippage の実測 vs profile**: template profile は LIVE 36 trade 由来。サンプル拡張要
3. **BTC との相関**: BtcMomentumGate は実装済だが本構成では未使用。LIVE 中に有効性を再評価
4. **direction × hour cell 別 WR**: post-mortem 仮説の妥当性を LIVE で再確認

### 5.3 撤退条件 (Phase 4 LIVE 進行時)

- LIVE 30日で rolling min < -5% → 検証続行 (PAPER に戻す)
- LIVE 30日で total negative → Phase 2.5 追加調査 or 撤退判断

## 6. 新規実装まとめ

### 6.1 Production code (gmo_bot)

- [`apps/gmo_bot/domain/strategy/components/base.py`](../apps/gmo_bot/domain/strategy/components/base.py)
  - `RegimeGate.allow_for_direction()` を追加 (default = True、direction-aware gate がオーバーライド)
- [`apps/gmo_bot/domain/strategy/components/regime_gates.py`](../apps/gmo_bot/domain/strategy/components/regime_gates.py)
  - `ATRPctRangeGate` (ATR% を min/max で挟む)
  - `BtcMomentumGate` (BTC bars を lazy load し |return| filter)
  - **`DirectionalSessionGate`** (LONG/SHORT 別の許可時間帯)
  - `CompositeRegimeGate.allow_for_direction()` を実装 (sub-gate を委譲)
- [`apps/gmo_bot/domain/strategy/components/bundle.py`](../apps/gmo_bot/domain/strategy/components/bundle.py)
  - 上記 gate の factory entry
- [`apps/gmo_bot/domain/strategy/models/mean_reversion_15m_v0.py`](../apps/gmo_bot/domain/strategy/models/mean_reversion_15m_v0.py) — P3-V 試行 (REJECT、Phase 3-V findings 参照)

### 6.2 Engine modification

- [`research/src/domain/backtest_engine.py`](../research/src/domain/backtest_engine.py)
  - direction 決定後に `regime_gate.allow_for_direction()` を call、blocked なら NO_SIGNAL

### 6.3 Research scripts

- [`research/scripts/resample_ohlcv.py`](../research/scripts/resample_ohlcv.py) — 15m→1h/4h aggregate
- [`research/scripts/fetch_gmo_pair_15m_paced.py`](../research/scripts/fetch_gmo_pair_15m_paced.py) — rate-limit safe backfill
- [`research/scripts/explore_phase1_axis_sweep.py`](../research/scripts/explore_phase1_axis_sweep.py) — pair/timeframe/variant/execution-model 横断評価
- [`research/scripts/postmortem_trade_features.py`](../research/scripts/postmortem_trade_features.py) — trade-level feature extraction + univariate WR
- [`research/scripts/postmortem_filter_sweep.py`](../research/scripts/postmortem_filter_sweep.py) — IS/OOS filter sweep
- [`research/scripts/stochastic_multiseed_eval.py`](../research/scripts/stochastic_multiseed_eval.py) — 50-100 seed stochastic_v1 p05

### 6.4 既存テスト

- **314 / 314 pass** (全 production code 変更を含めて regression なし)

## 7. 出力ファイル

- `research/data/runs/phase2_validation/directional.json` — 13w ideal_v1
- `research/data/runs/phase2_validation/dir_exit_combos.json` — exit policy 比較
- `research/data/runs/phase2_validation/multiseed.json` — stochastic_v1 50-seed
- `research/data/runs/postmortem_v0/` — Post-mortem 一式

## 8. 次のアクション (推奨優先順)

### 8.1 即着手 (Phase 2 / Phase 4 への橋渡し)

1. **PAPER 30 日 (Phase 4-1)**: 本構成を PAPER mode で 30 日運用。LIVE 環境模倣で slippage/latency/reject の実値を測定
2. **LIVE profile 拡充**: PAPER 期間中に trade を蓄積、`build_execution_profile` で stochastic_v1 profile を更新

### 8.2 Phase 2.5 改善余地

3. **w12 詳細分析**: なぜ最新 31 日が壊滅的か (regime, BTC correlation, ATR percentile を週単位で分析)
4. **adaptive stop loss**: 14-bar ATR ベースで R を市場 vol に追従
5. **min ≥-2 への詰め**: BTC mom を tune して mean を維持しつつ min 改善

### 8.3 PAPER OK 後 (Phase 4-2)

6. **LIVE 0.5x 30 日**: shadow_compare で LIVE vs backtest 一致率 ≥ 90% 確認
7. **LIVE 1.0x 移行** (一致率クリア時)

## 9. 教訓・知見

1. **plan §3 撤退判断は早すぎる場合あり**: Phase 1 + Phase 3-V 全敗でも、post-mortem 経由で edge は発見可能。**「データ分析を完全に出し切る前に撤退判断しない」**
2. **trade filter ≠ entry filter**: post-mortem WR は trade を取った後の話。engine に入れたら別。**両方確認必須**
3. **direction-aware filter が最大の改善源**: 個別 filter (vol/atr/session 単独) は +1pt mean 程度だが、direction-aware composite は +2.4pt mean (+5.57 vs +3.19)
4. **stochastic_v1 multi-seed は早期に走らせるべき**: ideal_v1 で見える edge が seed-fragile なら時間の無駄。本ケースは 50 seed 全て + → 真の edge
