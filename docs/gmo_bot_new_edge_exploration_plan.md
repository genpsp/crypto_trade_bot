# gmo_bot 新エッジ探索計画

- 対象: `apps/gmo_bot`（GMO コイン / SOL/JPY ほか）
- 位置づけ: `v2_dir_session_vol_time120` を LIVE 運用中（出血は止まっている前提）で、**追加のエッジ源**を探索するための計画
- 前提探索: trend-following / mean-reversion 系統は枯らし済み。詳細は [gmo_bot_exploration_findings.md](gmo_bot_exploration_findings.md) §3（棄却した系統）
- 作成: 2026-05-30

## 0. 出発点

### 0.1 現行 LIVE 戦略

[research/models/gmo_ema_pullback_15m_both_v0/config/current.json](../research/models/gmo_ema_pullback_15m_both_v0/config/current.json)

- `variant_id = v2_dir_session_vol_time120`
- `strategy.name = ema_trend_pullback_15m_v2`
- components:
  - regime_gate: composite（`directional_session` + `volume_confirmed` 0.4×）
  - exit_policy: `time_exit` 120 bar
- direction = BOTH / `leverage_multiplier = 1.0`
- cutover 時実績: Phase1 mean +5.57 / pos_rate 69.2% / stochastic_v1 50-seed p05 = +62.13%

### 0.2 結論済みの事実（再採掘しない領域）

| 系統 | 結果 | 出典 |
| --- | --- | --- |
| SOL/JPY 15m trend-follow（EMA pullback / Supertrend / Donchian） | edge decay・Gate A=0、Supertrend/Donchian は壊滅 | S4 findings |
| Track A–E（exit / regime / trend / entry / sizing の組み替え） | 最良でも v0 を僅差超え、Gate A 未達 | S4 findings |
| mean-reversion（BB 逆張り、SOL/BTC/ETH 15m + SOL 1h） | 全構成 REJECT（systematic adverse selection） | Phase3-V findings |

→ **「SOL/JPY 15m の OHLCV を組み替える」探索空間は枯渇**。新エッジはロジック組み替えではなく **入力の軸を変える**ことで取りに行く。

## 1. 再利用できる基盤資産

新エッジ探索は以下を土台にゼロから作らずに進められる。

- **5層 component framework**（[bundle.py](../apps/gmo_bot/domain/strategy/components/bundle.py)）: regime_gate / entry model / stop / exit / sizing を **config だけで組み替え可能**
- **per-bar ExitPolicy engine**: partial close 会計込み。LIVE の `exit_order_monitor` と同一 exit を共有（shadow_compare の前提）
- **sweep harness**: 宣言的 YAML + walk-forward/rolling/holdout + bootstrap CI + DSR + レジーム分解 + stochastic 執行モデル + Gate A/B/C
- **高速探索スクリプト**: 30k bar × 10 window を 2–3 分（O(N) precompute cache）
- **回帰安全網**: 314 テスト、v0 byte 一致の reproduction test

## 2. 基盤の制約（新エッジ探索のボトルネック）

| 制約 | 箇所 | 影響 |
| --- | --- | --- |
| データ層が **OHLCV 専用** | [source_registry.py](../research/src/data/source_registry.py)（`OhlcvProviderProtocol` のみ） | funding / ベーシス / 板情報を入力にできない |
| engine が **単一資産・単一ポジション** | `research/src/domain/backtest_engine.py` | クロスセクション / ポートフォリオ戦略を直接表現できない |
| GMO 登録 pair は SOL/JPY・BTC/JPY・ETH/JPY | `_GMO_PAIRS` | 他 pair は CSV 整備＋登録が必要 |

### 2.1 手元データ在庫

`research/data/raw/`:

- SOL/JPY: 15m（1y / to_2026_05）, 1h, 4h
- BTC/JPY: 15m（1y）
- ETH/JPY: 15m（1y）
- SOL/USDC（DEX 用）: 15m / 2h ほか

## 3. 探索トラック（EV / コスト順）

優先順位は「基盤再利用度 × 期待 EV ÷ 実装コスト」で決める。

### Track ①: 上位足（最優先・最小コスト）

**仮説**: v0/v2 の構造は 15m の noise に弱いだけで、1h/4h なら edge が残る。

- 既存 `ema_trend_pullback_15m_v2` bundle を `signal_timeframe = 1h / 4h` で rolling 評価
- データ（`soljpy_1h_to_2026_05.csv` / `soljpy_4h_to_2026_05.csv`）も揃っている
- **追加コード ほぼゼロ**（sweep YAML の dataset / timeframe 差し替えのみ）
- 注意: mean-reversion 1h は検証済みで失敗（Phase3-V §1.2）。trend 系の 1h/4h は未検証

**Done 基準**: rolling pos_rate ≥ 85% かつ min PnL ≥ 0%、holdout `total_scaled_pnl_pct_ci_low > 0`

### Track ②: レジーム切替メタ戦略（小コスト）

**仮説**: 単一ロジックでは chop と trend の両立ができない。レジームで entry を切り替える上位層なら両取りできる。

- 既存の `mean_reversion_15m_v0`（chop）と `ema_trend_pullback_15m_v2`（trend）を **regime で entry をルーティング**
- 必要なのは entry ルーター（小さい meta コンポーネント）。gate は `ADXGate` / `DonchianWidthGate` / `ATRPctRangeGate` が既存
- 単体では両方 REJECT でも、排他適用で adverse selection を相殺できるかを検証

**Done 基準**: 各レジーム window で構成戦略 single の PnL を下回らず、全体 mean が現行 v2 を上回る

### Track ③: クロスセクション / 相対強弱（中コスト）

**仮説**: SOL/BTC/ETH の相対強弱・ペアスプレッドに、単一資産では見えない edge がある。

- BTC/ETH/SOL の 15m CSV は揃っている
- relative-strength ランキング or pair-spread の単純版を先に当てる
- engine が単一資産のため、**マルチ資産評価ループ**の追加が必要（まず research 側だけで PoC）

**Done 基準**: バスケット/スプレッドの rolling Sharpe が単一 SOL/JPY を上回り、DSR p < 0.10

### Track ④: クロスアセット先行（リード/ラグ）（小〜中コスト）

**仮説**: BTC の動きが SOL/JPY に先行する。

- `BtcMomentumGate`（[regime_gates.py](../apps/gmo_bot/domain/strategy/components/regime_gates.py)）が既にあり、これを gate ではなく **entry 信号**へ拡張
- BTC の N bar リターン符号・閾値で SOL/JPY エントリーを起動

**Done 基準**: lead-lag entry が v2 baseline の rolling mean を +1pt 以上

### Track ⑤: 新データ次元（最大 EV・最大コスト・本命）

**仮説**: OHLCV 系が原理的に見えない **funding / ベーシス（perp-spot 乖離）** に構造的に別の α源がある（Phase3-V §4E で未踏と明記）。

- データソース追加（`source_registry` は OHLCV 専用 → funding/basis source の抽象を追加）
- engine が第2時系列を feature として読む経路を追加
- まず **GMO で funding 相当の系列が取得可能か**の調査から（取れなければ外部取引所の funding/basis を proxy にできるか）

**Done 基準**: funding/basis シグナル単体の long-short backtest が Gate A 相当を通過、または現行戦略の filter として mean を +2pt

## 4. 推奨ロードマップ

最小コスト・最大情報量の順:

1. **Track ① を即実施**（数時間）: 既存コードで 1h/4h sweep を回す。長期足での復活有無を確定
2. **Track ②/④ を並行**（小コスト）: 既存 component の組み替えで PoC
3. **Track ③** を PoC（research 側のマルチ資産ループだけ先に）
4. ①〜④ が渋ければ **Track ⑤** へ投資（データ整備が本丸、天井は最も高い）

各 Track は独立に検証可能で並列実験できる。

## 5. 撤退条件（早期 falsification）

| トラック | 撤退条件 |
| --- | --- |
| ① | 1h/4h いずれも rolling mean が v2(15m) を下回る → 上位足での復活は無し |
| ② | レジーム排他適用でも各 window で single 戦略を上回れない → メタ層は SOL/JPY に効かない |
| ③ | 相対強弱/スプレッドの rolling Sharpe が単一資産未満 → クロスセクションに edge 無し |
| ④ | lead-lag entry が marginal 1pt 未満 → BTC 先行性は 15m では取れない |
| ⑤ | funding/basis 単体が無相関 → OHLCV 外データでも GMO エコシステムに edge 無し |
| 全体 | ①〜⑤ 全滅 → GMO/SOL での新規 edge 探索を凍結、現行 LIVE 維持のみ |

## 6. 検証ガード（全 Track 共通）

新候補を LIVE に進める判断は主観でなく Gate A/B/C（[README.md](../README.md) §Backtest validity gates）で行う。

- Gate A: holdout 主軸の CI / DSR / walk-forward / レジーム分解
- Gate B: PAPER 30 日 + shadow_compare（trade 一致率 ≥ 95%）
- Gate C: `position_size_multiplier = 0.5` で 30 日 → 本サイズ
