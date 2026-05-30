# gmo_bot 戦略探索 結果サマリ（SOL/JPY 15m）

> 2026-05 までの探索（logic exploration S2–S4 → post-kill Phase 1/2/3-V → post-mortem）の結論を 1 本に集約したもの。
> 個別 findings（phase1/phase2/phase3-v/postmortem/s4）は本ファイルに統合済み。詳細な per-window テーブルが要る場合は git history を参照。
> 前提の探索計画は [gmo_bot_logic_exploration_plan.md](gmo_bot_logic_exploration_plan.md)（component 設計）、続く探索は [gmo_bot_new_edge_exploration_plan.md](gmo_bot_new_edge_exploration_plan.md)。

## 0. 一行結論

SOL/JPY 15m の OHLCV を組み替える探索空間は枯渇した。trend-follow / mean-reversion / 上位足 / 別 pair いずれも Gate 未達。**唯一見つかった edge が現行 LIVE の `v2_dir_session_vol_time120`**（trade-level post-mortem から direction-aware gate を発見）。

---

## 1. 現行 LIVE 構成（採用された唯一の edge）

`v2_dir_session_vol_time120` — cutover 2026-05-22（手順は [runbook/gmo_v2_cutover.md](runbook/gmo_v2_cutover.md)）。

```python
strategy: ema_trend_pullback_15m_v2
components:
  regime_gate:
    type: composite
    gates:
      - type: directional_session            # LONG/SHORT 別の許可時間帯
        long_allowed_utc_hours:  [15..23, 0..8]   # = JST 0-17（JST 夕方の LONG を除外）
        short_allowed_utc_hours: [3..20]          # = JST 12-05（JST 朝の SHORT を除外）
      - type: volume_confirmed
        period: 20
        volume_multiplier: 0.4                 # entry vol >= 0.4 * MA(20)
  exit_policy:
    type: time_exit
    max_holding_bars: 120
```

### 採用根拠（SOL/JPY 15m 1y, rolling 13 windows × 3000 bars, ideal_v1）

| 指標 | v0 baseline | v2_dir_session_vol_time120 | Phase 2 基準 |
| --- | ---: | ---: | --- |
| rolling mean | +3.19 | **+5.57** | ≥+5 ✓ |
| rolling pos_rate | 61.5% | 69.2% | ≥80% △ |
| rolling min | -9.30 | -8.01 | ≥-2% ✗ |
| holdout 6w total | -21.84 | **+12.73** | ≥+10 ✓ |
| break-even WR margin | — | **+8.63pt** | ≥+5 ✓ |
| stochastic_v1 50-seed p05 | — | **+62.13%（50/50 seed が total +）** | positive ✓✓ |

- strict には 3/7 pass / 2 borderline / 2 fail だが、**stochastic 50 seed 全て total positive** → 実行ノイズに堅牢。PAPER/LIVE 移行の十分条件と判断。
- 残課題: 最新 31 日 window（w12 -8.01）の大幅損失。LIVE では 5-day rolling kill-switch でカット（runbook §3）。

---

## 2. edge 発見の経緯（post-mortem）

trend-follow（Phase 1）+ mean-reversion（Phase 3-V）が全敗した後、「edge は必ずある」という指摘で **問題定義を見直し**、v0 が生成した 624 trade を trade-level で事後分析した。

### 効いた discriminator（univariate WR spread）

| 特徴量 | 勝つ条件 | spread |
| --- | --- | ---: |
| **direction × hour** | SHORT × 深夜(52.1%) ⇄ SHORT × 朝(26.6%) | **25pp** |
| holding_bars | q5(31+ bar) 50.0% ⇄ q1(1-5 bar) 26.0% | 24pp |
| btc_ret_4bar（U字） | BTC が明確に動く端 ⇄ 中央 | 15pp |
| volume_ratio_20 | >=0.36 ⇄ <0.137 | 13pp |

### engine 反映時の要点

- **direction-aware composite gate が最大の改善源**（+2.4pt mean）。個別 filter 単独は +1pt 程度。
- **Volume filter は orthogonal**（v0 に volume guard が無いため純粋に効く）。ATR/Session 単独は既存 v0 ガード（`long_atr_pct_max=0.7`）と二重作用して逆効果。
- v0 OOS mean -3.64 → filter OOS mean +0.91（**+4.55pt の OOS 改善**）。OOS が IS を上回る = curve fit でなく真の edge。

---

## 3. 棄却した系統（再試行しないための記録）

全て同一の rolling 評価枠（SOL/JPY 15m は 13w×3000bar、ideal_v1）。Done 基準は概ね pos_rate≥85 / min≥0 / mean が v0 超え。

| 系統 | 代表結果 | 判定 | 出典 |
| --- | --- | --- | --- |
| **Track A**（exit: BE / Partial / Chandelier / Time） | 最良 v2_A4_only mean +2.54（v0 +2.23）。min は改善するが mean ほぼ横ばい | Gate A=0 | S2 |
| **Track B**（regime gate: ADX / Donchian width / Equity filter） | 最良 B5+A4 min -3.16（tail 最小）だが mean +0.11 と劣化 | Gate A=0 | S3 |
| **Track C**（trend 入替: Supertrend / Donchian） | **壊滅**。Supertrend 4 / Donchian 4 variant 全て train PnL 30% 未満、rolling mean -6〜-11。chop でフリップ連発し WR 31-33% | 確定棄却 | S4 |
| **Track D**（entry: Volume-confirmed / Session） | D1 Volume 1.2x が唯一 v0 mean 超え（+2.84, +0.61pt）→ v2 に採用。Session は全滅 | 部分採用 | S4 |
| **Track E**（sizing: loss-streak / vol-target） | DD 改善ありだが mean 改善せず | Gate A=0 | S3 |
| **上位足 1h/4h**（v0/v2/Supertrend/Donchian） | 1h は mean +0.94 で 15m baseline(+2.23) より悪化。4h は param 不整合で no-trade | REJECT | Phase1 |
| **別 pair BTC/ETH 15m**（v0/v2） | BTC mean -0.59 / ETH mean -2.97。pair-specific decay 仮説を否定、構造劣化を支持 | REJECT | Phase1 |
| **mean-reversion（BB 逆張り）** SOL/BTC/ETH 15m + SOL 1h | 12 構成全て mean 負・pos_rate 0-33%。BB extremes 逆張りは **systematic adverse selection**。chop filter(ADX)強化で更に悪化 | 全構成 REJECT | Phase3-V |

→ exit / regime / trend / entry / sizing の組み替えも、別 timeframe・別 pair・別系統（逆張り）も Gate 未達。
**「SOL/JPY 15m OHLCV を組み替える」軸は枯渇**。次の edge は入力軸を変える方向（[new_edge_plan](gmo_bot_new_edge_exploration_plan.md)）。

---

## 4. 永続資産（撤退とは別に残った再利用可能成果物）

### コードベース
- **5層 component framework**: `RegimeGate / EntrySignal / StopPolicy / ExitPolicy / SizingPolicy` の ABC + 具体実装。config だけで組み替え可能（[components/bundle.py](../apps/gmo_bot/domain/strategy/components/bundle.py)）。設計は [logic_exploration_plan.md §2](gmo_bot_logic_exploration_plan.md)。
- **per-bar ExitPolicy engine**: partial close 会計込み。LIVE の `exit_order_monitor` と同一 exit を共有（shadow_compare の前提）。
- **regime gate 種別**: ADX / Donchian width / Equity curve / Session / Volume confirmed / ATRPctRange / **DirectionalSession** / BtcMomentum。
- **exit policy 種別**: FixedR / BreakEven / Time / PartialTp / Chandelier / Composite。
- **strategy slot**: `ema_trend_pullback_15m_v2` / `supertrend_15m_v0` / `donchian_breakout_15m_v0` / `mean_reversion_15m_v0`（registry 数行で追加可能に）。

### 探索インフラ
- スクリプト: `explore_track_a_*` / `explore_track_b_regime_gates` / `explore_track_d_entry_variants` / `explore_phase1_axis_sweep`（pair/timeframe/variant 引数化） / `postmortem_trade_features` / `postmortem_filter_sweep` / `stochastic_multiseed_eval` / `resample_ohlcv` / `fetch_gmo_pair_15m_paced`。
- 30,000 bar × 10 window を 2-3 分で評価（O(N²)→O(N) precompute + id(bars) cache）。

### 修正したバグ（探索の前提として S1 で潰した）
- A.1 walk_forward の CSV gap 耐性（`gap_tolerance_bars=16`。SOL/JPY 15m の 1198 gap で walk_forward window が 0 生成だった）
- A.2 stochastic_v1 を profile 無しで使うと ideal_v1 と等価になる → fail-fast 化
- A.3 trade parquet の `entry_regime` 空 dict 修正
- A.4 listed sweep の `name:` silent drop 修正
- ADX Wilder smoothing 発散バグ / partial close の portfolio_quote 二重更新会計バグ

### 回帰安全網
- 全期間で **314/314 テスト pass**。S1 の reproduction test で component 分解後も v0 と byte-level 一致を担保。

---

## 5. 出力ファイル（research/data/runs/）

- `phase1_axis_sweep/{soljpy_1h_all,soljpy_4h_all,btcjpy_15m,ethjpy_15m}.json`
- `phase2_validation/{directional,dir_exit_combos,multiseed}.json`
- `phase3_v/{soljpy_15m,soljpy_1h,btcjpy_15m,ethjpy_15m}.json`
- `postmortem_v0/{trade_features.csv,report.md,filter_sweep.md,ablation.json,btc_combo.json}`

入力 CSV は `research/data/raw/`（SOL/JPY 15m/1h/4h, BTC/JPY 15m, ETH/JPY 15m）。

---

## 6. 教訓

1. **「edge ゼロ」は研究の不充分の兆候**: H1〜H3 全否定でも、trade-level 分析（n=624）を skip していた。十分なサンプルがあれば discriminator は見つかる。
2. **trade filter ≠ entry filter**: 事後分析の WR は trade を取った後の話。engine に組込むと結果が変わる。両方で確認必須。
3. **OOS > IS は強いシグナル**: curve fit でない根拠。逆に ideal で見える edge が seed-fragile なら無駄なので stochastic multi-seed を早期に走らせる。
4. **filter は既存ガードとの相互作用を見る**: ATR/Session 単独が効かなかったのは v0 既存ガードとの二重作用。
5. **早すぎる撤退判断をしない**: Phase 1 + Phase 3-V 全敗でも post-mortem 経由で edge を発見できた。
