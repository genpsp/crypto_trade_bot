# gmo_bot 新エッジ探索 結果サマリ

> 探索計画は [gmo_bot_new_edge_exploration_plan.md](gmo_bot_new_edge_exploration_plan.md)。
> 本ファイルは各 Track の検証結果（accept/reject）を集約する。per-window の生データは `research/data/runs/` 配下の JSON を参照。

## Track ①: 上位足（1h / 4h）— REJECT（2026-05-30）

**仮説**: v0/v2 の構造は 15m の noise に弱いだけで、1h/4h なら edge が残る。

**結論**: 棄却。LIVE 採用の directional 構成を 1h/4h で初めて評価したが、edge は 15m baseline を大きく下回る。撤退条件（「1h/4h いずれも rolling mean が v2(15m) を下回る」）に該当。

### 前提の訂正

findings §3 の「上位足 1h/4h v0/v2 → REJECT（Phase1）」で評価された "v2" は **directional gate 発見前の `v2_default_bundle`** だった（[phase1_axis_sweep/soljpy_1h_all.json](../research/data/runs/phase1_axis_sweep/soljpy_1h_all.json) の variant 一覧で確認）。post-mortem 由来の最終 LIVE 構成 `v2_dir_session+vol+time120` は 1h/4h で未評価だったため、本 Track で実施した。

### 1h 結果（SOL/JPY, 10 windows × 1000 bars, ideal_v1）

出力: [track1_higher_tf/soljpy_1h_directional.json](../research/data/runs/track1_higher_tf/soljpy_1h_directional.json)

| variant | mean | pos_rate | min | 対 15m v2 |
| --- | ---: | ---: | ---: | --- |
| v0_baseline | +0.94 | 60% | -3.69 | — |
| v2_default_bundle | +0.94 | 60% | -3.69 | （15m: +3.19 系） |
| v2_dir_session | +1.05 | 50% | -3.69 | |
| v2_dir_session+vol | +0.98 | 50% | -3.69 | |
| **v2_dir_session+vol+time120**（=LIVE） | **+0.98** | **50%** | -3.69 | **15m: +5.57 / 69.2%** |
| v2_dir_session+vol+btc_mom+time120 | +0.82 | 20% | -1.23 | |

- **directional gate の寄与が 15m の +2.4pt → 1h では +0.1pt** に縮小（+0.94 → +1.05）。エッジがほぼ消失。
- Done 基準（rolling pos_rate ≥ 85% かつ min PnL ≥ 0%）を全構成が大幅に未達（pos 50% / min -3.69）。
- 1h windows は 1000 bar ≈ 41 日で 15m windows（3000 bar ≈ 31 日）より長い calendar をカバーするのに PnL が低い → 比較は 1h に有利な側でも負け。

### 4h 結果 — 構造的に no-trade

出力: [track1_higher_tf/soljpy_4h_directional.json](../research/data/runs/track1_higher_tf/soljpy_4h_directional.json)

- as-is（directional 構成）: **0 trade**（8 windows × 300 bars）。
- param 緩和プローブ（upper_trend を日足 1440min・weak_trend 720min・`max_distance_from_ema_fast_pct`=4.0・`long_atr_pct_max`=100）でも **依然 0 trade**。
- 支配的な no-signal 理由（2400 bar 集計）: `REGIME_GATE_BLOCKED_BY_COMPOSITE_GATE`(477) / `EMA_TREND_FILTER_FAILED`(288) / `ATR_STOP_CONFLICT_MAX_LOSS`(225, swing-low stop 距離が 1.2% risk 上限を超過) / `CHASE_ENTRY_TOO_FAR_FROM_EMA` ほか。
- 15m 較正の entry+risk サーフェス（composite gate × chase 距離 × swing-low stop vs 1.2% risk cap）が 4h では全条件同時成立する bar を 1 つも生まない。取引を起こすには entry/risk 全体の再較正＝実質新規戦略が必要で、Track ①（dataset/timeframe 差し替えのみ）の範囲外。

### なぜ復活しないか（解釈）

採用 edge の本体は **direction × hour-of-day**（SHORT×深夜 ⇄ SHORT×朝で WR 25pp）。上位足では (1) qualifying setup 数が 4–16 分の 1 に減り、(2) hour-of-day の粒度が粗くなるため、エッジが希釈される。エッジは 15m タイムスケール固有の現象で、上位足にリスケールしない。

→ **上位足での復活は無し**。次は Track ②（レジーム切替メタ）/ Track ④（BTC リード/ラグ entry）へ。

## Track ②: レジーム切替メタ — REJECT（2026-05-31）

**仮説**: 単一ロジックでは chop と trend の両立ができない。ADX でレジームを分け entry を排他ルーティング（trend→ema / chop→MR）すれば、各 single の不利レジームを除外し adverse selection を相殺できる。

**結論**: 棄却。routing は v2 を相殺どころか大きく希釈する。

実装: 新 strategy `regime_router_15m_v0`（[models/regime_router_15m_v0.py](../apps/gmo_bot/domain/strategy/models/regime_router_15m_v0.py)）。per-bar で ADX を見て `>= router_adx_trend_min` なら `ema_trend_pullback`、未満なら `mean_reversion` の entry に委譲。出力: [track2_4_meta/soljpy_15m.json](../research/data/runs/track2_4_meta/soljpy_15m.json)。

| variant | mean | pos% | min | trades | 対 v2 |
| --- | ---: | ---: | ---: | ---: | --- |
| ema_v2_baseline（LIVE 同枠再計算） | **+5.57** | 69% | -8.01 | 398 | — |
| mean_reversion_single | -1.25 | 31% | -6.90 | 1484 | （Phase3-V REJECT 再現） |
| router_live_bundle（trend=v2-gated / chop=MR） | **+0.13** | 54% | -11.06 | 1224 | **-5.44pt** |
| router_null_bundle（純ルーティング） | -1.90 | 23% | -12.43 | 1540 | -7.47pt |

- 全体 mean +0.13 << v2 +5.57、min も悪化（-11.06）。Done 基準（全体 mean が v2 超え／各 window で single 以上）を満たさない。
- ADX 閾値感度（router_adx_trend_min ∈ {10,15,25,35,45,60}）でも最良 +0.15（thr=15）で v2 に遠く及ばず。
- **なぜ効かない**: v2 のエッジ本体は direction × hour-of-day で、ADX レジームと**直交**している。v2 は adx<25 の bar でも directional gate で稼いでいる。それを chop として MR に回すと、(1) v2 が取れていた利益を捨て、(2) MR は chop でも negative-EV（Phase3-V の systematic adverse selection）を持ち込むため、二重に劣化する。「trend と chop を分ける」前提自体が SOL/JPY のエッジ構造に合っていない。

## Track ④: BTC リード/ラグ entry — REJECT（2026-05-31）

**仮説**: BTC の動きが SOL/JPY に先行する。BtcMomentumGate を gate でなく **entry 信号**に拡張し、BTC の N bar リターン符号で SOL entry を起動する。

**結論**: 棄却。BTC 方向単体は SOL entry のタイミングにならない。

実装: 新 strategy `btc_leadlag_15m_v0`（[models/btc_leadlag_15m_v0.py](../apps/gmo_bot/domain/strategy/models/btc_leadlag_15m_v0.py)）。BTC bars を open_time(UTC) で整合し、`btc_ret >= +thr → LONG / <= -thr → SHORT`、stop は ATR ベース、TP は R 倍。

| variant | mean | pos% | min | trades | 対 v2 |
| --- | ---: | ---: | ---: | ---: | --- |
| btc_leadlag_0_5 | **+0.22** | 54% | -22.64 | 1330 | -5.35pt |
| btc_leadlag_0_3 | -3.41 | 38% | -20.44 | 1597 | -8.98pt |

- 最良 +0.22 で v2 baseline+1pt（=+6.57）に遠く及ばず（撤退条件「marginal 1pt 未満」に該当）。
- BTC 方向だけで SOL を建てると tail が大きく（min -22.64）勝率も 38% 台。BtcMomentum は post-mortem で示された通り **SOL 自身の setup に対する確認 gate** としてのみ有効で、primary entry 信号としては機能しない。

## 実装インフラ memo（Track ②/④ で判明）

component-bundle 系の新 strategy を追加する際は backtest_engine.py の **2 つのハードコード allowlist 両方**に名前を入れないと silent degrade する:

- `_strategy_uses_component_bundle`: 入れないと `strategy.components`（regime_gate / exit_policy）が無視される
- `_resolve_ohlcv_limit`（`_STRATEGIES_REQUIRING_15M_UPPER_TREND_LIMIT`）: 上位足 EMA を使う strategy が入っていないと decision 窓が 300 になり `UPPER_TREND_EMA_NOT_STABLE` 永久ループ（router が ema leg を一切発火できなかった原因）

本 Track で `regime_router_15m_v0` を両方へ、`btc_leadlag_15m_v0` を前者へ追加。回帰 330/330 pass（v0 byte 一致維持）。

## Track ③: クロスセクション / 相対強弱 — REJECT（net）/ 探索唯一の gross edge（2026-05-31）

**仮説**: SOL/BTC/ETH の相対強弱・スプレッドに、単一資産では見えない edge がある。

**結論**: グロスでは強い edge が実在する（探索全体で初）が、必要回転数が高く **GMO 小売コストでは非経済的**。net では単一 SOL を下回り Done 未達。

実装: engine は単一資産のため research 側に独立のマルチ資産ループを新規実装（[scripts/explore_track3_cross_sectional.py](../research/scripts/explore_track3_cross_sectional.py)）。SOL/BTC/ETH 15m を共通タイムスタンプで整合（37,879 bar / 2025-03-27〜2026-05-20）。lookback L で順位付けし long-short バスケット（最強/最弱）を H bar 保有。momentum/reversal × (L,H) を 24 構成 sweep（DSR の n_trials=24）。出力: [track3_xs/soljpy_basket.json](../research/data/runs/track3_xs/soljpy_basket.json)。

### gross（cost 0）— 短期 cross-sectional reversal に明確な edge

| config | ann Sharpe | DSR p | roll_mean | roll_min | roll_pos% | total% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **xs_rev_L4_H4**（1h lookback / 15m rebalance, 勝ち空売り） | **+7.07** | **0.000** | +7.46 | +2.92 | **100%** | +2858 |
| xs_rev_L16_H4 | +2.84 | 0.117 | +2.92 | -2.94 | 85% | +261 |
| sol_buyhold（baseline） | -0.16 | 0.565 | +0.04 | -6.83 | 62% | -36 |
| sol_tsmom_L16_H16（baseline） | +0.80 | 0.203 | +0.91 | -5.42 | 62% | +41 |

- reversal 系が momentum 系を全 horizon で上回る = peer 比で動き過ぎた資産が短期で戻る（cross-sectional short-term reversal）。最短 horizon ほど強い → マイクロ構造/bid-ask bounce 由来。
- 全 13 窓 positive・DSR p≈0 で gross は統計的に頑健。

### net（コスト感度）— 損益分岐 片道 ≈1.5bps

| config | 0bps | 1bps | 2bps | 3bps | 5bps | 7bps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| xs_rev_L4_H4 | +7.07 | +1.72 | -3.63 | -8.95 | -19.47 | -29.74 |
| xs_rev_L16_H4 | +2.84 | +0.10 | -2.63 | -5.36 | -10.73 | -15.98 |
| sol_buyhold | -0.16 | -0.16 | -0.16 | -0.16 | -0.16 | -0.16 |

- xs_rev_L4_H4 は片道 ~1.5bps で break-even（9468 rebalance × turnover）。GMO 小売（taker ~5bps + slippage 3bps = 7-8bps）では深く負。
- 7bps では全 24 構成が負で、最良でも sol_buyhold(-0.16) 未満 → **Done 基準（net rolling Sharpe が単一 SOL 超え）未達・撤退条件に該当**。

### 含意

- 探索全体で **唯一 DSR p≈0 の本物の edge** が出た領域。ただし現行 GMO taker 執行コストの天井（~7bps）に対し必要コスト（~1.5bps）が 1/4 以下で、現行 LIVE bot には寄与しない。
- monetize には maker rebate / 低手数料venue / market-making 的執行が前提で、本探索（GMO SOL/JPY directional）のスコープ外。低コスト執行経路を持てた場合に再訪する価値あり（pair-spread 版も同じ reversal family で同じコスト壁に当たる見込み）。

## Track ⑤: 新データ次元（funding / basis）— REJECT（Done 未達）/ filter は marginal+（2026-05-31）

**仮説**: OHLCV 系が原理的に見えない funding / perp-spot basis に構造的に別の α 源がある。

**結論**: (arm1)funding 単体は有意な edge 無し、(arm2)v2 の filter として +2pt には遠く及ばず。Done 未達。ただし funding tail-filter は探索中の外部信号で唯一 net-additive で、v2 の pos_rate を Phase2 基準まで押し上げる副次効果あり。

### データ取得可能性

- **GMO native: funding/basis は無い**。GMO API client 表面は `/v1/ticker` `/v1/klines` `/v1/symbols` `/v1/account/margin` `/v1/order` 系のみ（[gmo_api_client.py](../apps/gmo_bot/adapters/execution/gmo_api_client.py)）。GMO Coin のレバレッジは日次レバレッジ手数料モデルで perpetual funding rate も perp-spot basis も提供しない。`source_registry` も OHLCV 専用（funding/basis 実装は repo 全体にゼロ）。
- **外部 proxy: 取得可能**。Binance USDⓈ-M `fapi/v1/fundingRate`（認証不要・public）で SOLUSDT funding(8h) + markPrice を取得。1368 点 / 2025-03-01〜2026-05-30（評価期間を完全カバー）を [sol_funding_binance_8h.csv](../research/data/raw/sol_funding_binance_8h.csv) にキャッシュ。

### arm1: funding 単体の予測力 — 無し

- corr(funding, forward 8h return): SOL/JPY **-0.032** / SOL markPrice(USD) **-0.031**（符号は逆張り方向で正しいが t≈-1.0 で非有意）。
- 極値分位逆張り long-short（bottomQ long / topQ short、8h 保有、cost 7bps）: SOL/JPY ann Sharpe **+0.01** DSR p 0.75 / SOL markUSD **+0.33** DSR p 0.63（buy&hold -0.31）。Gate A 相当を通過する standalone edge 無し。
- funding は 8h と低頻度なので Track ③ のようなコスト壁は無い（回転は問題でない）。問題は単純にシグナルが弱いこと。

### arm2: v2 の filter として — +0.23pt（+2pt 未達）

`FundingGate`（[regime_gates.py](../apps/gmo_bot/domain/strategy/components/regime_gates.py)）を実装。逆張り tail filter: `funding > high_threshold`→LONG ブロック / `funding < low_threshold`→SHORT ブロック。bar 時刻以前の最新 funding を参照（lookahead 無し）。13×3000 / ideal_v1。

| variant | mean | pos% | min | trades |
| --- | ---: | ---: | ---: | ---: |
| v2_baseline | +5.57 | 69% | -8.01 | 398 |
| **v2 + funding(+0.0001/-0.0002)** | **+5.80** | **85%** | -8.01 | 388 |
| v2 + funding(tight 0/0) | +2.47 | 69% | -9.72 | 145 |
| v2 + funding(+0.00005/-0.0001) | +3.46 | 62% | -4.67 | 257 |

- 緩い tail filter は 10 trade 除くだけで mean +0.23pt、**pos_rate 69%→85%**（v2 が borderline fail だった Phase2 基準 ≥80% を超える）、min 不変。tight にすると良 trade を削って劣化。
- Done arm2「+2pt」には遠く及ばず（+0.23pt）→ Track ⑤ は Done 未達で **REJECT**。
- ただし ②④ が destructive だったのに対し funding は唯一 **marginal positive** な外部信号。新エッジではないが「v2 の pos_rate を基準まで上げる軽量 robustness filter」候補として別途検討価値あり（LIVE 投入には Gate A/B/C 必須、外部 API 依存＝執行系の冗長化要）。

### basis 未検証

markPrice(perp USD) は CSV にキャッシュ済で perp-spot basis PoC は可能だが、近縁の crowding 信号である funding が弱いため basis も同程度の見込み。優先度は低い。

## 探索全体の結論（①〜⑤）

| Track | 系統 | 結果 |
| --- | --- | --- |
| ① 上位足 | 1h/4h | REJECT（15m 固有、上位足で消失） |
| ② レジーム切替メタ | ADX router | REJECT（軸が直交、v2 を希釈） |
| ④ BTC リード/ラグ | entry 信号 | REJECT（BTC 方向は entry にならない） |
| ③ クロスセクション | RS reversal | **gross edge 実在（Sharpe+7/DSR≈0）** / net はコスト壁で REJECT |
| ⑤ funding/basis | 外部 proxy | REJECT（単体 edge 無し / filter +0.23pt）。funding tail-filter のみ marginal+ |

**total**: directional な新エッジは ①②④⑤ で出ず、v2 の `direction×hour` エッジが依然唯一の採用 edge。新たに分かったのは (a) 真の統計的 edge は cross-sectional short-term reversal に存在するが現行 GMO taker コストでは取れない、(b) 外部 funding は v2 の pos_rate を基準まで上げる軽量 filter になりうる、の 2 点。plan §5 撤退条件「①〜⑤ 全滅 → 新規 edge 探索を凍結、現行 LIVE 維持のみ」に概ね該当。次に投資するなら低コスト執行経路（③ の monetize 前提）が最も天井が高い。
