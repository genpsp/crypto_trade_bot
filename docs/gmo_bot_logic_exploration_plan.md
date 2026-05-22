# gmo_bot 戦略ロジック探索計画

- 対象: `apps/gmo_bot` / SOL/JPY 15m
- 前段: baseline 診断と strategy search v1/v2 は実施済み・後続探索で supersede されたため 2026-05-22 に整理。必要な前提事実は本書 §0 と `docs/baselines/gmo_ema_pullback_15m_both_v0__2026-05-16.md` に集約。
- スコープ: **「パラメータの値」ではなく「戦略ロジックそのもの」の探索**。entry / exit / regime / sizing の各レイヤーで構造的に置き換え可能な仮説を列挙し、検証順序を定める

## 0. 出発点となる事実（パラメータ探索で判明したもの）

ロジック改修の必要性を裏付ける主要証拠:

1. **bimodal exit が脆い**: 全 trade が `-1R` か `+2.2R` のどちらかで終了し、partial / trailing が無い。break-even WR 31.25% に対し train 36.2% / holdout 25.9% — margin が薄く、WR が 3pt 落ちると即赤字
2. **chop regime で即反転 SL を量産**: LIVE 期間 27 trade のうち 6 trade が 5 bar 以内に SL。価格レンジ 14% で oscillation。エントリー判定（pullback → reclaim）が chop で破綻
3. **trend filter のエッジが decay している**: 2025-06〜09 は rolling 30%+ → 2025-10〜2026-02 は -6% 台。EMA9/34 cross の trend 定義そのものに賞味期限が切れている可能性
4. **SHORT は filter を効かせると trade=3、緩めると WR 0%**: ロジックが SOL/JPY 反発を捉えられていない（trend follower の SHORT 経路は構造的に弱い）
5. **regime tag は trade に付くが、戦略は regime を読んでいない**: gate に regime 情報を渡せていない

→ **値の最適化では break-even margin を 3〜4pt 改善する程度が天井**。WR のドリフトに対する耐性を上げるには **exit / regime / signal のどれかを構造ごと替える**必要がある

## 1. 探索の 5 トラック

優先順位順。各トラックは独立に検証可能（並列実験できる）。

### Track A: Exit logic の刷新（最優先）

**仮説**: 現行の「固定 R 倍 TP / swing low SL」を **多段化 / トレーリング化**することで、WR が 28% の局面でも避けられる。

| ID | 仮説 | 期待効果 | 実装難度 |
| --- | --- | --- | --- |
| A1 | **Break-even stop**: 価格が +1R に到達したら stop を建値に移す | 「+1R 到達後に SL」になる trade を 0 に近づける。avg R の底上げ | 小 |
| A2 | **Partial TP @ 1R**: ポジション 50% を 1R で利確、残り 50% を 2.5R or trailing | WR を維持しつつ、伸びる trade を温存。psychological も改善 | 中 |
| A3 | **Chandelier trailing stop**: 直近高値 - ATR × 2.5 で stop を引き上げる（TP 固定なし） | 強トレンド時に伸ばす / 弱トレンド時に早く撤退 | 中 |
| A4 | **Time-based exit**: N bars 経過しても TP/SL のどちらにも到達していなければ break-even or 現値で手仕舞い | 「ダラダラ含み損」を圧縮、capital 回転率向上 | 小 |
| A5 | **Volatility-adjusted TP**: TP 倍率を `atr_pct` で動的決定（高ATR=遠TP / 低ATR=近TP） | レンジ相場で TP を 1.2R まで縮めることで chop でも当てる | 中 |

**評価メソッド**:
- 同一の entry シグナル（`L_combo_v1` baseline）で exit のみ差し替えて A/B
- 同じ rolling 13 windows で min / pos_rate / mean を比較
- 採択基準: **rolling min PnL が 0% 以上 / mean PnL が L_combo_tp22 以上 / pos_rate ≥ 90%**

### Track B: Regime gate の追加（高優先）

**仮説**: chop / trend / shock の **regime を自動検出してエントリーを止める or 戦術を切り替える** ことで edge decay を回避できる。

| ID | 仮説 | 実装難度 |
| --- | --- | --- |
| B1 | **ADX gate**: `ADX < 20` の chop 区間を no-trade、`20-40` でフルポジ、`>40` で size 0.5 | 小 |
| B2 | **Donchian width gate**: 過去 24h の `(high-low)/close` が ATR の N 倍以下＝chop と判定して skip | 小 |
| B3 | **Range / trend bar 比率**: 過去 96 bar のうち「実体 < ATR × 0.5」のローソク比率が 60% 以上＝chop | 中 |
| B4 | **HMM / 簡易 regime classifier**: 価格・vol・出来高で 3 states（trend up / trend down / chop）を分類しエントリーを許可 | 大 |
| B5 | **Walk-forward edge filter**: 直近 N trade の avg R が負なら新規エントリーを 24h 停止（equity curve filter） | 小 |

**評価メソッド**:
- 既存の `regime_tagger` の trend bucket とは別レイヤー（gate は entry 前、tagger は事後）
- 採択基準: **chop window の PnL が -1% 以内、trend window の PnL は維持（劣化 1pt 以内）**

### Track C: Trend detection の代替（中優先）

**仮説**: EMA9/34 cross は 15m SOL/JPY では遅すぎる/早すぎる可能性。別の trend 識別器に置換することで signal-to-noise を改善できる。

| ID | 仮説 | 実装難度 |
| --- | --- | --- |
| C1 | **Supertrend(10, 3)**: ATR バンド + flip 判定。EMA gate を置換 | 小 |
| C2 | **Donchian breakout(20)**: 20 bar 高値ブレイクで LONG / 安値ブレイクで SHORT。pullback は不要に | 中 |
| C3 | **Ichimoku cloud**: 雲の上＝LONG only、雲下＝SHORT only、雲内＝no-trade | 中 |
| C4 | **HMA(20) slope**: HMA の slope sign で trend 判定、slope 加速で entry | 中 |
| C5 | **Linear regression channel**: 50 bar の lin reg の slope と R² で trend 強度を量化 | 中 |

**評価メソッド**: entry signal だけ差し替えて、exit は現行 + Track A の優勝案を併用

### Track D: Entry signal variant（中優先）

**仮説**: 「pullback → reclaim」以外の entry トリガーで edge を取り直す。

| ID | 仮説 | 実装難度 |
| --- | --- | --- |
| D1 | **Volume-confirmed reclaim**: 現行 reclaim の bar で `volume > 1.5 × ma(20)` を必須化 | 小 |
| D2 | **RSI divergence**: 価格が前回高値を更新したが RSI が更新していない bear divergence で SHORT（chop でも効く） | 中 |
| D3 | **Mean reversion 枝**: chop regime 判定時のみ、EMA fast から ATR×1.5 離れた bar で逆張り（trend strategy と排他） | 中 |
| D4 | **Multi-bar pullback の質**: pullback bars の low が EMA fast を貫通した深さ・速度を信号化 | 中 |
| D5 | **Session filter**: UTC 0-6 / 6-12 / 12-18 / 18-24 のうち、edge のある session のみエントリー | 小 |

### Track E: Sizing / capital allocation（quick win あり）

**仮説**: 同じ entry/exit ロジックでも、sizing を regime に応じて変えれば DD を半減できる。

| ID | 仮説 | 実装難度 |
| --- | --- | --- |
| E1 | **Loss streak filter**: 直近 N trade の連敗が K 回以上で size 0.5 / K+M 回で 0 | 小 |
| E2 | **Vol-target sizing**: ポジション size を `target_vol / atr_pct` に動的決定 | 小 |
| E3 | **Equity curve filter**: 累積 PnL が直近 30 trade で最大値から -X% drawdown なら size 0.5 | 中 |
| E4 | **Kelly fraction (rolling)**: 直近 50 trade の WR / R から Kelly を計算、その半分を使用 | 中 |

## 2. 検証アーキテクチャ（実装すべき抽象化）

ロジック多数の組み合わせを評価するために、現コードの **直線的な evaluate 関数を 4 レイヤーに分離**する。

```
┌────────────────────────────────────────────────────────────────┐
│  evaluate_strategy_for_model(direction, bars, config)          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ RegimeGate.allow(bars, config) -> bool / state          │  │  ← Track B/E
│  ├──────────────────────────────────────────────────────────┤  │
│  │ EntrySignal.evaluate(bars, config) -> Decision          │  │  ← Track C/D
│  ├──────────────────────────────────────────────────────────┤  │
│  │ StopPolicy.initial_stop(decision, bars, config) -> stop │  │  ← Track A
│  ├──────────────────────────────────────────────────────────┤  │
│  │ ExitPolicy.update(position, bar, config) -> Action      │  │  ← Track A
│  │   - BE_STOP, PARTIAL_TP, TRAIL, TIME_EXIT, HOLD          │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ SizingPolicy.size_multiplier(state, config) -> float    │  │  ← Track E
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 必要な実装

| パッケージ | 役割 | 既存コード |
| --- | --- | --- |
| `apps/gmo_bot/domain/strategy/components/regime_gates.py` | `ADXGate`, `DonchianWidthGate`, `EquityFilterGate` | 新規 |
| `apps/gmo_bot/domain/strategy/components/entry_signals.py` | `EmaPullbackSignal`, `SupertrendSignal`, `DonchianBreakoutSignal` | 既存 evaluate を分解 |
| `apps/gmo_bot/domain/strategy/components/stop_policies.py` | `SwingLowStop`, `ChandelierStop`, `FixedAtrStop` | 既存 `swing_low_stop` を吸収 |
| `apps/gmo_bot/domain/strategy/components/exit_policies.py` | `FixedRExit`, `PartialTpExit`, `TrailingExit`, `TimeExit` | **`backtest_engine` 側でループする必要あり（今は entry 時に TP/SL 固定）** |
| `apps/gmo_bot/domain/strategy/components/sizing_policies.py` | `FixedSize`, `VolTargetSize`, `LossStreakSize` | 一部 `risk_constants` から移動 |
| `research/src/domain/backtest_engine.py` | per-bar で ExitPolicy.update を呼ぶループに改修 | 大規模改修 |

### 2.2 互換性

- 既存 model `ema_trend_pullback_15m_v0` は `EntrySignal=EmaPullbackSignal, StopPolicy=SwingLowStop, ExitPolicy=FixedRExit, RegimeGate=None, SizingPolicy=AtrRegimeMultiplier` の組合せで再現できることをテストで担保
- 新規 model は `strategy.name = "ema_pullback_v2_trail"` のような bundle 名で registry に登録
- Firestore config の `strategy.components` キーで個別 policy を指定可能にする（実験時の差し替えを config 駆動で）

### 2.3 backtest_engine 改修ポイント

現行は entry 時に `stop_price`, `take_profit_price` を fix して per-bar で touch 判定するだけ。  
これを **per-bar に ExitPolicy.update(position, bar) -> ExitAction** を呼ぶ形に変える:

```python
for bar in bars_after_entry:
    action = exit_policy.update(position, bar, atr_at_entry, ...)
    match action:
        case BREAK_EVEN: position.stop_price = position.entry_price
        case TRAIL(new_stop): position.stop_price = new_stop
        case PARTIAL_TP(fraction, price): close_partial(...)
        case CLOSE(price, reason): close_full(...)
        case HOLD: continue
    # touch 判定 (stop / TP) は従来通り
```

→ これにより A1〜A5 を policy 差し替えだけで全部実装できる。  
→ `apps/gmo_bot/infra/execution/exit_order_monitor.py` 側も同じ ExitPolicy を呼ぶ形にすれば LIVE と backtest が同じ exit ロジックを共有できる（**shadow_compare の前提条件**）

## 3. 検証ロードマップ（5 スプリント、各 ~1 週）

### S1: 基盤改修（探索の土台）

| | task | 出力 |
| --- | --- | --- |
| S1.1 | `walk_forward` の gap 耐性追加（`split_contiguous_segments` に `gap_tolerance_bars` 引数） | walk_forward 90/30/30 が 4 window 以上生成される |
| S1.2 | `ExitPolicy` / `StopPolicy` / `RegimeGate` / `SizingPolicy` の抽象クラスと既存ロジックの分解 | 既存テスト全 pass、現行 model の挙動が完全一致 |
| S1.3 | `backtest_engine` を per-bar ExitPolicy 呼び出し対応 | 上記 |
| S1.4 | LIVE 36 trade から `build_execution_profile` 実行、stochastic_v1 を実体化 | `research/data/execution_profiles/gmo_soljpy.json` |
| S1.5 | sweep YAML の `strategy.components.*` 上書きスキーマ追加 | sweep 構成のサンプル |

**Done基準**: 現行 `gmo_long_combo_v1` を新フレームで再現し、v3 sweep と完全一致の数値が出ること

### S2: Track A（exit policy 探索）

最大インパクト見込みのため最初に着手。

| | 候補 | 評価 |
| --- | --- | --- |
| S2.1 | A1 (BE stop @ 1R) 単体 | rolling pos_rate / min |
| S2.2 | A4 (time exit, N ∈ {30, 60, 120}) 単体 | 上記 |
| S2.3 | A2 (partial TP 50% @ 1R + rest @ {2.0R, 2.5R, trail}) | 上記 |
| S2.4 | A3 (Chandelier ATR × {2, 2.5, 3}) | 上記 |
| S2.5 | A5 (vol-adjusted TP: low_vol=1.2R, mid=2.0R, high=2.6R) | 上記 |
| S2.6 | 優勝案を `L_combo_v1` の exit と差し替えて統合評価 | gate A 評価 |

**Done基準**: いずれかの exit 案で **rolling 13 windows の `min PnL > 0`** を達成（現行は -1.34%）

### S3: Track B（regime gate）+ Track E（sizing）

S2 と並行可能。

| | 候補 |
| --- | --- |
| S3.1 | B1 (ADX gate, threshold 15/20/25) |
| S3.2 | B2 (Donchian width gate) |
| S3.3 | B5 (equity filter, lookback 30 trade) |
| S3.4 | E1 (loss streak filter, threshold {3, 5}) |
| S3.5 | E2 (vol-target sizing, target_atr_pct ∈ {0.4, 0.6, 0.8}) |
| S3.6 | B + E の組合せ評価（直交性チェック）|

**Done基準**: chop 期 rolling window（2025-10→2026-02 など）の PnL を最低 +3pt 改善

### S4: Track C（trend detection）+ Track D（entry variants）

S2/S3 で出口・regime が固まった上で、ロジック中核の入れ替えを試す。

| | 候補 |
| --- | --- |
| S4.1 | C1 (Supertrend(10, 3)) entry 差し替え |
| S4.2 | C2 (Donchian breakout 20-bar) entry 差し替え |
| S4.3 | D1 (volume-confirmed reclaim) |
| S4.4 | D5 (session filter, UTC 時間帯別 marginal) |
| S4.5 | C × Track A/B 統合候補で full sweep |

**Done基準**: いずれかの新 entry で **train PnL +50% / rolling pos_rate 85%+ / DSR p < 0.10**

### S5: 候補絞り込みと PAPER 投入

- S2〜S4 の優勝候補を 2〜3 つに絞る
- データを 2024-01 まで遡及拡張、train 標本を 1y → 2y に
- stochastic_v1 + 実 profile で再評価
- Gate A 通過候補のみ PAPER 30 日へ
- 同時に基盤の shadow_compare を整備（実 LIVE vs backtest 一致率測定）

## 4. 仮説の falsification（早期撤退）

各仮説に kill criteria を設定し、無駄な深堀りを避ける:

| 仮説群 | 撤退条件 |
| --- | --- |
| Track A 全体 | A1〜A5 のどれも `L_combo_v1` の rolling min を 0 まで持ち上げられない → exit 改修では解けない問題、Track B/C へリソース移動 |
| Track B | ADX/Donchian-width gate で chop window が改善しても trend window の PnL が 5pt 以上劣化 → regime gate そのものが SOL/JPY 15m に向いていない |
| Track C | Supertrend/Donchian で train PnL がいずれも 30% 未満 → trend detection の入れ替えでは解けない、Track D のシグナル設計へ |
| Track D | volume / divergence / session 全てが marginal 1pt 未満 → 15m bar の情報量では entry quality を上げられない、上位時間軸 (1h) ベースへ転換 |
| 計画全体 | S1〜S4 終了時点で **Gate A pass 候補 0** → SOL/JPY 15m での EMA pullback 系統そのものを廃止、別 pair（BTC/JPY, ETH/JPY）への戦略移植 or 完全別系統（grid trading / market making / option-like）へ撤退検討 |

## 5. リスクと前提

### 5.1 大きいリスク

- **`backtest_engine` の per-bar ExitPolicy 改修は LIVE の `exit_order_monitor` と同期が必要**。両者が乖離すると shadow_compare が破綻し、本計画全体の評価信頼性が崩れる。S1 で両者を同じ ExitPolicy で動かす契約を最初に固める
- **stochastic_v1 profile を作る LIVE 標本が 36 trade しかない**。本来は 100+ 欲しいが、無いものは仕方ないので S1.4 では「直近100 trade」になり次第 profile を再生成する運用にする
- **Track C/D は entry 構造を変えるため `tests/test_gmo_ema_trend_pullback_15m_strategy.py` 系のテストが大量に壊れる**。新 model は別ファイル / 別 strategy.name で導入し、既存テストを壊さない

### 5.2 並行で進める運用タスク

- LIVE 縮退（BOTH → LONG-only / size 0.5）は本計画と独立に**今日から**実施（出血を止める）
- `apps/gmo_bot/adapters/execution` の `GMO executions payload invalid` 連発・MAINTENANCE 22回の I/O 問題（revision_plan §5）は本計画の前提として並行で潰す

### 5.3 撤退判断のタイミング

- S2 終了時点（~2 週後）でも rolling min が改善しなければ、Track C/D に重心を移す
- S4 終了時点で Gate A 候補 0 のままなら、SOL/JPY 15m 戦略そのものから撤退して別 pair / 別 timeframe / 別系統に切り替える（§4 計画全体の kill criteria）

## 6. 採用判定（Definition of Done）

戦略ロジック刷新の最終ゴール:

1. **rolling 13 windows で `pos_rate ≥ 90% かつ min PnL ≥ 0%`**（現行 `L_combo_v1` は 92.3% / -1.34%）
2. **直近 6 ヶ月 walk-forward の test PnL が ≥ +5% / 6 windows 中 4 windows 以上 positive**
3. **holdout（narrow LIVE 期間）の `total_scaled_pnl_pct_ci_low > 0` かつ DSR p < 0.10**
4. **break-even WR からの margin ≥ 6pt**（現行 3pt）
5. **stochastic_v1 + 実 profile で seed p05 が positive**

上記 5 つをすべて満たした候補のみ PAPER 30 日 → LIVE 0.5x 30 日に進む。

---

## 付録 A: 探索中に発見した研究基盤のバグ（S1 で同時に潰す）

ロジック探索を進める前提として、以下のバグを S1（基盤改修）で潰す。それぞれ Gate 評価 / 検証信頼性に直接影響している。

### A.1 walk_forward が CSV gap で生成 0 になる（high severity）

- **症状**: `research/sweeps/gmo_15m_baseline_sensitivity.yaml` や v1/v2/v3 sweep で `windows: - type: walk_forward, train_days: 180, test_days: 90, step_days: 90` を指定しても、trial parquet には walk_forward window が 1 つも出ない（holdout のみ）
- **原因**: [research/src/eval/window.py:68-79](../research/src/eval/window.py#L68-L79) の `split_contiguous_segments` が **15分以外の gap を 1 bit でも検出すると即セグメント分割**。SOL/JPY 15m の CSV には 1198 件の gap（80%が 30分）があり、結果として 1199 個の micro-segment に分割される（最大 86 bar）。180+90 日 = 25,920 bar の窓を満たすセグメントが存在しないため `_build_walk_forward_windows` が空配列を返す
- **影響**:
  - revision_plan の Gate A 中の `walk_forward_positive_ratio` 評価が **そもそも常に欠損**していた
  - baseline run の `walk_forward_positive_ratio` カラムは全 trial で `null`
- **修正案**:
  - `split_contiguous_segments(close_times, expected_minutes, gap_tolerance_bars=16)` を追加し、`gap_tolerance_bars × expected_minutes` 以下の gap は同一セグメント扱いとする
  - `_build_walk_forward_windows` 側にも同じ tolerance を伝播
  - default は `gap_tolerance_bars=16`（15m × 16 = 4h、市場の典型的なメンテ gap を許容）
- **テスト**: 1198 gap 入りの SOL/JPY 15m CSV に対し、180/90/90 で **少なくとも 4 windows** が出ることを assertion

### A.2 stochastic_v1 が profile 無しで ideal_v1 と等価になる（medium severity）

- **症状**: baseline run（`20260516-075500-gmo_15m_baseline_sensitivity-42119a6`）で seeds=[1,2,3,4,5] を指定したが、**5 seed 全てが完全に同一の trade を生成**。stochastic の意味がない
- **原因**: [research/src/eval/execution_model.py:194-228](../research/src/eval/execution_model.py#L194-L228) の `StochasticExecutionModel` は profile 無しでは `p_reject=0 / latency=0 / slippage_bps=0` にフォールバックする。`additional_slippage_bps` も entry/exit に同じ値を加えるだけなので、RNG の影響を受けるパスが事実上存在しない
- **影響**:
  - baseline sweep の 270 trial のうち **216 trial が seed dimension で重複**していた（実質 54 trial 分の情報量）
  - `seed_count`, `*_seed_p05`, `stochastic_seed_p05_ci_positive` Gate A check が無意味
- **修正案**:
  - `build_execution_model` で `model_id == "stochastic_v1"` かつ profile が空のとき **`ValueError` を投げる**（fail fast）
  - 代替として、CLI 側で warning ログ：`[research] WARN: stochastic_v1 with empty profile is equivalent to ideal_v1`
  - `research.scripts.build_execution_profile` の入力 schema を docstring に追記し、profile JSON の minimal example を `research/data/execution_profiles/_template.json` として配置
- **前提タスク**: LIVE 36 trade を JSON にダンプする CLI（`apps/gmo_bot/scripts/dump_live_trades_for_profile.py` 新規）

### A.3 trade parquet の `entry_regime` が空 dict のままになる（medium severity）

- **症状**: v2 sweep の trade parquet (`--keep-trades all`) を読むと `entry_regime` が `{}`。trial summary の `by_regime` キーには trend/volatility/btc_corr の bucket 別 PnL が入っているのに、trade 単位ではタグが落ちている
- **影響**: trade-level の by-regime drilldown ができない（v2 deep dive で "trend/volatility/btc_corr の bucket 表示が Empty" になった件）
- **修正案**:
  - `research/src/domain/backtest_engine.py` の trade 生成箇所で `regime_tagger.get_bar_regime(...)` の結果を `BacktestTrade.entry_regime` に格納する経路を確認（現在は entry時に渡せているはずだが、ある条件で空になる）
  - 再現テスト: v2 spec を `--keep-trades all` で smoke 実行し、trade parquet の `entry_regime` 列の非空率 ≥ 99%
- **緊急度**: ロジック探索の Track B 評価で chop / trend 別 PnL を出すために必須。S1.2 のコンポーネント分解と一緒に直す

### A.4 listed sweep の case `name:` が silently drop される（low severity）

- **症状**: `combinations: listed` で各 case に `name: my_pretty_name` を書いても、trial parquet の `case_name` には `direction=LONG,exit.take_profit_r_multiple=2.2,...` のような **override path の文字列が出てしまう**。YAML の `name:` は完全に無視される
- **原因**: [research/src/sweep/grid.py:7-22](../research/src/sweep/grid.py#L7-L22) の `expand_cases` で `case["values"]` のみを `expanded` に追加、`case["name"]` は読まれない。`format_case_name` も sorted override path を join しているだけ
- **影響**: 表示用ニックネームを付けられず、debug / レポートの可読性が低下。v2 doc で `nicknames = {...}` を Python 側で持つハメになった
- **修正案**:
  - `expand_cases` の戻り値を `list[ExpandedCase]`（`overrides`, `name`, `tags`）に変更
  - `TrialSpec.tags['case_name']` に `case.name or format_case_name(case.overrides)` を入れる
  - `compare_runs` / `views.format_table` 側はそのまま動く
- **互換性**: 既存の axes ベースの spec は影響なし

### A.5 まとめ表

| ID | severity | file | S1 task |
| --- | --- | --- | --- |
| A.1 | high | `research/src/eval/window.py` | S1.1 walk_forward gap 耐性 |
| A.2 | medium | `research/src/eval/execution_model.py` | S1.4 stochastic profile 必須化 |
| A.3 | medium | `research/src/domain/backtest_engine.py` + `regime_tagger.py` | S1.2 component 分解と同時に修正 |
| A.4 | low | `research/src/sweep/grid.py` | S1.5 sweep schema 拡張に含める |

S1.1〜S1.5 はこれらの修正を吸収するように設計する。

---

## 付録 B: 既存ロジックの依存関係マップ

`evaluate_ema_trend_pullback_15m_v0` の 728 行は以下の関心事が混在している。S1 の分解作業ではこれを 4 components に切る:

| 行範囲 | 関心事 | 分解先 |
| --- | --- | --- |
| 269-292 | minimum bars / config validation | strategy entrypoint |
| 294-321 | market context (EMA / closes / highs / lows) | `EmaPullbackSignal._build_context` |
| 322-441 | upper timeframe trend gate (4h EMA cross, gap, slope, drift) | `UpperTrendGate` (regime gate) |
| 442-465 | EMA fast/slow filter | `EmaPullbackSignal.evaluate_trend` |
| 467-499 | pullback detection | `EmaPullbackSignal.evaluate_pullback` |
| 501-538 | reclaim / breakdown confirm | `EmaPullbackSignal.evaluate_reclaim` |
| 540-557 | distance from EMA gate | `EmaPullbackSignal.evaluate_chase` |
| 559-601 | RSI gate | `RsiGate` (entry filter) |
| 603-661 | ATR + size multiplier + stop calculation | `AtrSizing` + `SwingLowStopPolicy` |
| 663-712 | TP calculation | `FixedRExitPolicy` |

これだけで既に 4-5 components の責務に切れる。改修コストの見積もりはあるが、**ロジック探索の柔軟性を担保するには避けて通れない投資**。
