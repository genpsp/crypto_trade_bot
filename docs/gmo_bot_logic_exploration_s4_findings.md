# S4 Track C/D 探索結果 と S2〜S4 総括

- 対象: `apps/gmo_bot` SOL/JPY 15m / v2 component bundle
- データ: `research/data/raw/soljpy_15m_to_2026_05.csv`
- 期間: 直近 30,000 bar (~10 ヶ月) + rolling 10 windows × 3,000 bar
- 実行モデル: `ideal_v1`

## 1. Track D (entry signal variants)

### 1.1 実装した gate

| ID | クラス | 仕様 |
| --- | --- | --- |
| D1 | `VolumeConfirmedGate(period, volume_multiplier)` | 現在 bar の volume > N × MA(20) でないと entry 禁止 |
| D5 | `SessionGate(allowed_utc_hours)` | UTC hour が指定 set 外なら entry 禁止 |

### 1.2 結果 (rolling 10 windows × 3000 bar)

| case | min | mean | pos_rate% | 評価 |
| --- | --- | --- | --- | --- |
| v0_baseline | -9.30 | +2.23 | 50.0 | baseline |
| v2_A4_only | -8.15 | +2.54 | 50.0 | Track A 暫定勝者 |
| **D1_VolumeConfirmed_1_2x** | -9.09 | **+2.84** | **60.0** | **mean ベスト / pos_rate ベスト** |
| D1_VolumeConfirmed_1_5x | -10.48 | +1.60 | 60.0 | min 悪化 |
| D1_VolumeConfirmed_2_0x | -6.69 | +0.26 | 50.0 | mean 大幅悪化 |
| D5_Session_UTC_18to24 | -7.72 | +1.02 | 60.0 | min 改善, mean 悪化 |
| D5_Session_UTC_12to24 | -6.45 | +0.65 | 40.0 | min 改善, mean 悪化 |
| D5_Session_UTC_0to6 | -10.06 | -0.78 | 50.0 | 悪化 |
| D5_Session_UTC_6to12 | -7.57 | -1.99 | 40.0 | 悪化 |
| D5_Session_UTC_12to18 | -5.41 | -0.70 | 30.0 | 悪化 |
| D1_1_2x+A4 | -9.09 | +2.82 | 60.0 | D1 alone と同等 (A4 効果ほぼ無) |

### 1.3 観察

- **D1 Volume Confirmation @ 1.2x が初めて mean を v0 超え**: +2.84 (vs v0 +2.23, +0.61pt), pos_rate +10pt
- **D1 は閾値が高すぎると逆効果**: 1.5x で +1.60, 2.0x で +0.26 — 1.2x が sweet spot
- **D5 セッションフィルタはほぼ全パターン悪化**: UTC 12-24 (アジア後場〜EU/US 時間) のみ min を改善するが mean は v0 比劣化
- **D1 + A4 = D1 only**: A4 time exit の効果は D1 が trade を絞った後では誤差程度

### 1.4 Done 基準

計画書 §S4:
> 候補 | D1 (volume-confirmed reclaim) / D5 (session filter, UTC 時間帯別 marginal)
> Done基準 (S4): いずれかの新 entry で train PnL +50% / rolling pos_rate 85%+ / DSR p < 0.10

| 基準 | 目標 | D1_1_2x | Pass? |
| --- | --- | --- | --- |
| train PnL +50% | +50% | +21.34% (vs v0 +35.06%) | ❌ |
| rolling pos_rate ≥ 85% | ≥ 85% | 60% | ❌ |
| rolling mean 改善 | ≥ +1pt | +0.61pt | ❌ |

**Done 基準は未達**。ただし v0 baseline の rolling mean を上回る唯一の構成。

## 2. Track C (trend detection)

### 2.1 実装

[apps/gmo_bot/domain/strategy/models/supertrend_15m_v0.py](../apps/gmo_bot/domain/strategy/models/supertrend_15m_v0.py)
- Supertrend(period, atr_multiple) を新 `strategy.name = "supertrend_15m_v0"` として gmo registry に登録
- 上昇/下降フリップ bar での entry、Supertrend level を stop、TP は R-multiple
- schema / engine の strategy.name allow-list を拡張

### 2.2 結果 (rolling 10 windows × 3000 bar)

| case | closed (30k bar) | sum_scaled% | rolling min | rolling mean | pos_rate% |
| --- | --- | --- | --- | --- | --- |
| v0_baseline | 466 | +35.06 | -9.30 | +2.23 | 50.0 |
| **Supertrend(10, 3.0)** | 689 | **-113.11** | -35.72 | -10.80 | 30.0 |
| Supertrend(10, 2.0) | 995 | -80.78 | -33.55 | -6.87 | 40.0 |
| Supertrend(7, 3.0) | 683 | -84.96 | -19.81 | -7.54 | 10.0 |
| Supertrend(14, 3.0) | 662 | -70.74 | -32.10 | -6.15 | 40.0 |

### 2.3 観察

- **Supertrend は壊滅的**: 4 parameter variant 全てで rolling mean が -6% 以下
- **trade 数が v0 の 1.4〜2.1 倍**: SOL/JPY 15m の chop でフリップを連発、entry quality が崩壊
- **WR 31-33%**: v0 の 38.63% から 6pt 低下
- atr_multiple を上げる (10→14 period) ことで多少改善するが、それでも大赤字

### 2.4 Donchian breakout(N) 追補

[apps/gmo_bot/domain/strategy/models/donchian_breakout_15m_v0.py](../apps/gmo_bot/domain/strategy/models/donchian_breakout_15m_v0.py) も同じ枠組みで実装し検証。

| case | closed | wins | WR% | sum_scaled% (30k bar) | rolling min | rolling mean | pos_rate% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Donchian(20) | 866 | 305 | 35.22 | -39.98 | -23.98 | -4.16 | 40.0 |
| Donchian(10) | 954 | 335 | 35.12 | -43.91 | -28.73 | -6.01 | 40.0 |
| Donchian(30) | 775 | 284 | 36.65 | **+0.30** | -18.81 | -0.54 | 60.0 |
| Donchian(48) | 664 | 232 | 34.94 | -38.89 | -18.91 | -3.18 | 20.0 |

- 最良 (period=30) でも train PnL +0.30% で v0 (+35.06%) に遠く及ばない
- WR は 34-37% (v0 = 38.63%)
- rolling min は全 variant で v0 (-9.30) を大幅悪化 (-18 to -29)
- trade 数は v0 の 1.4〜2.0 倍 → chop でのブレイクアウト失敗を量産

### 2.5 計画書 §4 撤退条件

> Track C | Supertrend/Donchian で train PnL がいずれも 30% 未満 → trend detection の入れ替えでは解けない、Track D のシグナル設計へ

**Supertrend 4 variant + Donchian 4 variant = 8 variant 全てで train PnL 30% 未満**:
- Supertrend 最良: -70.74% (Supertrend(14, 3.0))
- Donchian 最良: +0.30% (Donchian(30))

**撤退条件に完全に該当** — Track C 系統での解決は確定的に不可。

## 3. S2〜S4 累積評価マトリクス

全 Track の rolling 10 windows ベース、`sum_scaled_pnl_pct` で比較:

| カテゴリ | 構成 | min | mean | pos_rate% | Δ vs v0 (mean) |
| --- | --- | --- | --- | --- | --- |
| baseline | v0_baseline | -9.30 | +2.23 | 50.0 | — |
| baseline | v2_default | -9.30 | +2.23 | 50.0 | 0 |
| **Track A 勝者** | v2_A4_only | -8.15 | +2.54 | 50.0 | +0.31 |
| Track A | A1 BE 1.0R | -9.75 | -0.37 | 40.0 | -2.60 |
| Track A | A2 Partial 50% @ 1R | -7.24 | +0.67 | 40.0 | -1.56 |
| Track A | A3 Chandelier ATR 2.5 | -6.46 | -2.00 | 20.0 | -4.23 |
| Track B | B1 ADX 20-60 | -13.47 | -0.56 | 60.0 | -2.79 |
| Track B | B2 Donchian w3 | -9.30 | +2.24 | 50.0 | +0.01 |
| Track B | B5 Equity curve lb20 | -5.00 | -0.08 | 40.0 | -2.31 |
| **Track B composite** | **B5+A4** | **-3.16** | +0.11 | 40.0 | -2.12 |
| Track B composite | B1+B5+A4 | -5.85 | +0.69 | 60.0 | -1.54 |
| **Track D 勝者** | **D1 Volume 1.2x** | -9.09 | **+2.84** | **60.0** | **+0.61** |
| Track D | D5 Session UTC 12-24 | -6.45 | +0.65 | 40.0 | -1.58 |
| Track D | D1 1.2x + A4 | -9.09 | +2.82 | 60.0 | +0.59 |
| Track C | Supertrend(14, 3.0) | -32.10 | -6.15 | 40.0 | -8.38 |

### 3.1 観察

3 つの異なる「勝ち方」が見えた:
1. **mean 重視**: D1_VolumeConfirmed_1_2x (mean +2.84, 唯一 v0 を mean で上回る)
2. **min 重視**: B5+A4 (min -3.16, tail-risk 最小)
3. **balance**: v2_A4_only (mean +2.54, min -8.15) — 改善は小さいが安全

**いずれも Done 基準を満たさない**:
- Done 基準: `pos_rate ≥ 90% かつ min PnL ≥ 0%` / `holdout total_scaled_pnl_pct_ci_low > 0`
- 最良の構成でも pos_rate 60%, min -3.16% (B5+A4) または -9.09% (D1_1.2x)

## 4. 計画書 §4 計画全体 kill criterion

> 計画全体 | S1〜S4 終了時点で Gate A pass 候補 0 → SOL/JPY 15m での EMA pullback 系統そのものを廃止、別 pair（BTC/JPY, ETH/JPY）への戦略移植 or 完全別系統（grid trading / market making / option-like）へ撤退検討

**Gate A pass 候補は 0**。kill criterion 該当。

### 4.1 撤退条件の意味

「SOL/JPY 15m での EMA pullback 系統そのものを廃止」とは:
- EMA9/34 + pullback/reclaim のロジック構造では SOL/JPY 15m で edge を維持できない
- exit policy / regime gate / entry filter / trend detection いずれの改修も Done 基準を満たすほどの改善を生まない
- 標的 pair / timeframe / 戦略カテゴリーの根本的な変更が必要

## 5. 撤退判断と次の選択肢

計画書 §5.3:
> S4 終了時点で Gate A 候補 0 のままなら、SOL/JPY 15m 戦略そのものから撤退して別 pair / 別 timeframe / 別系統に切り替える

### 5.1 撤退オプション

| 選択肢 | 説明 | コスト | 期待 |
| --- | --- | --- | --- |
| A. 別 timeframe | SOL/JPY 1h / 4h で再評価 | 中（コードはほぼ流用可、CSV だけ用意） | 中（v0 の構造が 15m の noise に弱いだけなら長期足で復活する可能性） |
| B. 別 pair | BTC/JPY / ETH/JPY 15m に戦略移植 | 中（broker 側の SOL/JPY 固定箇所が散在）| 中（SOL より trend 強の pair なら edge 残るかも） |
| C. 別系統（grid / MM） | 全く別の戦略カテゴリーへ | 大（新規実装） | 不明 |
| D. LIVE 縮退で運用継続 | size 0.5x, LONG-only で出血コントロール、新戦略開発と平行 | 小（既に推奨済 §5.2） | 低 (損失緩和のみ) |

### 5.2 最小コストで最大情報量を得る順序

1. **D を即実施**: LIVE で size 0.5x / LONG-only に縮退（出血止血）
2. **A 別 timeframe 検証** (1〜2 週間): 既存コードで signal_timeframe を 1h / 4h に切り替えて rolling 評価
3. **B 別 pair 検証** (2〜4 週間): BTC/JPY 15m への移植（broker adapter は GMO 同じ、pair 固定箇所を refactor）
4. A/B のどちらかが Done 基準を満たせばそちらへ移行、ダメなら **C 別系統** を検討

ただし最終撤退判断は本探索の枠を超える経営判断。本ドキュメントは「現行 EMA pullback v0 が SOL/JPY 15m で Done 基準を満たせない」事実の客観的記録に留める。

## 6. 一方で得られた価値ある成果物

撤退判断とは別に、本探索で得られた**永続的な技術資産**:

### 6.1 コードベース
- **コンポーネント分解アーキテクチャ** (S1.2): `RegimeGate` / `EntrySignal` / `StopPolicy` / `ExitPolicy` / `SizingPolicy` の ABC + 9 具体実装
- **per-bar ExitPolicy engine** (S1.3): partial close 会計含む。任意 exit logic を策略に差し替え可能
- **新 strategy slot 6 個** : `ema_trend_pullback_15m_v2` / `supertrend_15m_v0` / `donchian_breakout_15m_v0` を含めて、新戦略追加コストが registry の数行になった
- **regime gate 5 種**: ADX / Donchian width / Equity curve / Session / Volume confirmed
- **exit policy 6 種**: FixedR / BreakEven / Time / PartialTp / Chandelier / Composite

### 6.2 探索インフラ
- 探索スクリプト 4 個: `explore_track_a_exit_policies.py` / `explore_track_a_rolling.py` / `explore_track_b_regime_gates.py` / `explore_track_d_entry_variants.py`
- 各 case を 30,000 bar × 10 rolling window で 2-3 分で評価可能
- O(N²) → O(N) precompute + id(bars) cache（ADX / Donchian width / ATR）

### 6.3 修正したバグ（S1 付録 A）
- A.1 walk_forward の gap 耐性 (gap_tolerance_bars=16)
- A.2 stochastic_v1 profile 必須化（fail-fast）
- A.3 entry_regime 空 dict 修正
- A.4 listed sweep case name drop 修正
- ADX Wilder smoothing バグ（最初値 1000+ 発散していた）
- partial close の portfolio_quote 二重更新会計バグ

### 6.4 ドキュメント
- [docs/gmo_bot_logic_exploration_plan.md](gmo_bot_logic_exploration_plan.md) — 探索計画・component 設計の記録
- [docs/gmo_bot_logic_exploration_s4_findings.md](gmo_bot_logic_exploration_s4_findings.md) — Track C/D + S2〜S4 総括（本ファイル）

整理メモ (2026-05-22): `gmo_bot_logic_exploration_s2_findings.md` / `gmo_bot_logic_exploration_s3_findings.md` は本ファイルに結論が集約済みのため削除。詳細が必要な場合は git history を参照。

### 6.5 これらの再利用先

撤退して別 pair / 別 timeframe を試す場合、上記の **v2 component bundle と探索スクリプトは丸ごと使い回し可能**。新戦略を作るときは:
- 既存 component を組み替える (config だけで)
- 新しい entry signal を `apps/gmo_bot/domain/strategy/models/` に追加
- 探索スクリプトの `_build_cases()` を編集

開発速度は本探索開始時の 5-10 倍に短縮されている見込み。

## 7. テスト pass

全期間を通して 314 / 314 テスト pass を維持。S1 で実装した再現テストにより、コンポーネント分解後も v0 と byte-level 一致が保証されている。

```
.venv/bin/python -m pytest tests/ -q
# 314 passed in 7.17s
```
