# S3 Track B 探索結果（regime gate）

- 対象: `apps/gmo_bot` SOL/JPY 15m / v2 component bundle
- データ: `research/data/raw/soljpy_15m_to_2026_05.csv`
- 期間: 直近 30,000 bar (~10 ヶ月) + rolling 10 windows × 3,000 bar
- 実行モデル: `ideal_v1`
- 評価スクリプト: [research/scripts/explore_track_b_regime_gates.py](../research/scripts/explore_track_b_regime_gates.py)

## 1. 実装したゲート

| ID | クラス | 仕様 |
| --- | --- | --- |
| B1 | `ADXGate(period, min_adx, max_adx)` | ADX が [min_adx, max_adx] 外なら entry 禁止 |
| B2 | `DonchianWidthGate(donchian_period, atr_period, width_atr_threshold)` | 過去 N bar の (高値-安値)/ATR が閾値未満なら entry 禁止 |
| B5 | `EquityCurveGate(lookback_trades, min_trades)` | 直近 N trade の平均 R-multiple が負なら entry 禁止 |
| — | `CompositeRegimeGate(gates)` | 複数ゲートを AND で組合せ |

`gate_state["recent_r_multiples"]` を engine が runner close 時に append し、`EquityCurveGate` が読む。

## 2. 単一期間 (30,000 bar)

| case | closed | WR% | sum_scaled% | gate_blocked |
| --- | --- | --- | --- | --- |
| **v0_baseline** | 466 | 38.63 | **+35.06** | - |
| v2_default | 466 | 38.63 | +35.06 | - |
| v2_A4_only (Track A 勝者) | 467 | 38.97 | **+38.17** | - |
| B1_ADX20-60 | 325 | 34.77 | -12.70 | 7299 blocked |
| B1_ADX15-60 | 415 | 38.07 | +20.85 | 2379 blocked |
| B1_ADX25-60 | 222 | 35.14 | -6.70 | 14025 blocked |
| B2_Donchian_w3 | 466 | 38.63 | +35.10 | 78 blocked |
| B2_Donchian_w4 | 460 | 38.26 | +28.87 | 872 blocked |
| B2_Donchian_w5 | 423 | 37.35 | +9.95 | 3655 blocked |
| B5_EquityCurve_lb20 | 27 | 44.44 | +8.22 | 28102 blocked |
| B5_EquityCurve_lb30 | 38 | 44.74 | +11.02 | 27846 blocked |
| B1+B5_ADX20+Equity20 | 22 | 40.91 | +4.66 | 28474 blocked |
| B1+A4_ADX20+Time120 | 325 | 35.38 | -9.70 | 7312 blocked |
| **B5+A4_Equity20+Time120** | 27 | 44.44 | +8.22 | 28102 blocked |
| **B1+B5+A4** | 22 | 40.91 | +4.66 | 28474 blocked |

### 観察

- **ADX gate 単独は逆効果**: ADX [20, 60] は trend と見なせるが、SOL/JPY 15m の v0 シグナルは ADX < 20 (chop) でも勝ち trade があった。ゲートが勝てる場面を切ってしまっている。
- **Donchian width は微妙**: w=3 は 78 ブロック (ほぼ null)、w=5 で 3,655 ブロック+大きく劣化。本質的に v0 信号と冗長。
- **Equity curve gate は大半をブロック**: 28,102/30,000。連敗期間が長く、復帰した瞬間も新たな連敗で抜けられない。WR が +5.8pt (38.63→44.44) と劇的に改善するが、絶対 PnL は減る。
- **どのゲートも単一期間で v0 / A4 を上回らない**。

## 3. Rolling 10 windows × 3,000 bar

| case | min | mean | pos_rate% | 評価 |
| --- | --- | --- | --- | --- |
| v0_baseline | -9.30 | +2.23 | 50.0 | baseline |
| v2_A4_only | -8.15 | +2.54 | 50.0 | Track A 勝者 |
| B1_ADX20-60 | -13.47 | -0.56 | 60.0 | min 悪化 |
| B1_ADX15-60 | -13.57 | +1.13 | 40.0 | min 悪化 |
| B1_ADX25-60 | -9.62 | +0.05 | 40.0 | mean 大幅悪化 |
| B2_Donchian_w3 | -9.30 | +2.24 | 50.0 | null effect |
| B2_Donchian_w4 | -11.70 | +1.71 | 60.0 | min 悪化 |
| B2_Donchian_w5 | -13.47 | +0.02 | 50.0 | 悪化 |
| B5_EquityCurve_lb20 | -5.00 | -0.08 | 40.0 | min 改善, mean 大幅悪化 |
| B5_EquityCurve_lb30 | -5.00 | +1.06 | 40.0 | min 改善, mean 悪化 |
| B1+B5_ADX20+Equity20 | -5.85 | +0.39 | 50.0 | min 改善, mean 悪化 |
| B1+A4_ADX20+Time120 | -13.47 | -0.26 | 60.0 | min 悪化 |
| **B5+A4_Equity20+Time120** | **-3.16** | +0.11 | 40.0 | **min 最良 (+6.14pt 改善)**, mean 大幅悪化 |
| **B1+B5+A4** | **-5.85** | +0.69 | **60.0** | **min +3.45pt 改善 / pos_rate +10pt 改善** |

### 観察

- **B1+B5+A4 が最も bargain な構成**: rolling min -9.30→-5.85 (+3.45pt 改善)、pos_rate 50→60% (+10pt 改善)、mean +2.23→+0.69 (-1.54pt 悪化)
- **B5+A4 は min を -3.16% まで圧縮**: tail-risk 削減としては最強。ただし trade 機会が激減し mean は +0.11 まで落ちる
- **Equity curve gate が DD コントロールに効く** ことが確認: 22-27 trade まで絞ることでドローダウン窓を回避

## 4. Done 基準達成状況

計画書 §S3:
> 採択基準: chop window の PnL が -1% 以内、trend window の PnL は維持（劣化 1pt 以内）

| 基準 | 目標 | 最有力候補 (B1+B5+A4) | Pass? |
| --- | --- | --- | --- |
| chop window PnL ≥ -1% | ≥ -1% | -5.85% | ❌ |
| trend window 維持 | 劣化 ≤ 1pt | mean -1.54pt (大幅劣化) | ❌ |
| rolling min 改善 ≥ +3pt | +3pt | +3.45pt | ✅ |

**結論**: 厳密 Done 基準は未達。Track A + Track B では **rolling min の 0% 達成は無理**。

## 5. 計画書 §4 撤退条件との照合

> Track B | ADX/Donchian-width gate で chop window が改善しても trend window の PnL が 5pt 以上劣化 → regime gate そのものが SOL/JPY 15m に向いていない

trend window 劣化は最大 -1.54pt (B1+B5+A4 mean delta)。**5pt 以下なので撤退条件には該当しない**。

ただし mean が baseline を上回る Gate combination は出ていないため、**Track B 単独でも Done 基準には届かない**ことが確定。

> 計画全体 | S1〜S4 終了時点で Gate A pass 候補 0 → SOL/JPY 15m での EMA pullback 系統そのものを廃止

S2 + S3 終了時点で Gate A pass 候補は **0**。Track C (trend detection) または Track D (entry variants) で entry 品質を変える必要がある。

## 6. 次の一手

1. **Track C (trend detection)** に進む — Supertrend / Donchian breakout / HMA で entry シグナル自体を差し替え
2. **Track D (entry variants)** に進む — volume confirmation / RSI divergence / session filter
3. **B1+B5+A4 を default ベース**として残し、C / D の評価に組み合わせる

S3 終了時点での暫定ランキング:
1. v2_A4_only (mean +2.54, min -8.15) — bias-variance 良
2. v0_baseline (mean +2.23, min -9.30) — 何もしない
3. B1+B5+A4 (mean +0.69, min -5.85) — DD 控えめ重視なら
4. B5+A4 (mean +0.11, min -3.16) — tail-risk 最小化重視なら

## 7. 実装したもの

### コード
- [apps/gmo_bot/domain/strategy/components/regime_gates.py](../apps/gmo_bot/domain/strategy/components/regime_gates.py)
  - `ADXGate` / `DonchianWidthGate` / `EquityCurveGate` / `CompositeRegimeGate`
  - Wilder smoothing (RMA) を正しく実装、O(N) precompute + id(bars) cache
- [apps/gmo_bot/domain/strategy/components/base.py](../apps/gmo_bot/domain/strategy/components/base.py)
  - `RegimeGate.allow(... gate_state=...)` シグネチャ拡張
- [apps/gmo_bot/domain/strategy/components/bundle.py](../apps/gmo_bot/domain/strategy/components/bundle.py)
  - regime_gate types: `null_gate` / `adx` / `donchian_width` / `equity_curve` / `composite`
- [research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py)
  - entry signal 評価前に `bundle.regime_gate.allow()` を呼ぶ
  - `gate_state["recent_r_multiples"]` を runner close 時に append

### スクリプト
- [research/scripts/explore_track_b_regime_gates.py](../research/scripts/explore_track_b_regime_gates.py) — 15-case 単期間 + rolling 評価

### 生データ
- [research/data/processed/track_b_regime_gates_v2.json](../research/data/processed/track_b_regime_gates_v2.json)

### 副産物
- ADX の Wilder smoothing バグを修正（最初の実装で値が 1000 超に発散していた）
