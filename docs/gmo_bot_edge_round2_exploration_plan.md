# gmo_bot エッジ探索 Round 2 計画（執行コスト × ブレッドス）

- 対象: `apps/gmo_bot`（GMO コイン / SOL/JPY ほか）+ research 側マルチ資産ループ
- 位置づけ: Round 1（[gmo_bot_new_edge_exploration_plan.md](gmo_bot_new_edge_exploration_plan.md) / [findings](gmo_bot_new_edge_findings.md)）で **方向性アルファの探索が枯渇**したのを受け、軸を「ロジック組み替え」から **執行コスト × 銘柄ブレッドス**へ移す計画
- 前提: directional 系（trend/MR/上位足/router/lead-lag）は枯らし済み。LIVE は `v2_dir_session_vol_time120` を維持
- 作成: 2026-06-02

## 0. 出発点（Round 1 の確定事実）

| 領域 | 結論 |
| --- | --- |
| SOL/JPY 15m OHLCV のロジック組み替え（trend/MR/exit/regime/sizing） | 枯渇・全 Gate 未達 |
| 上位足 1h/4h ① | REJECT（15m 固有、上位足で消失） |
| レジーム router ② / BTC lead-lag ④ | REJECT（v2 の direction×hour と直交、希釈） |
| **cross-sectional short-term reversal ③** | **gross ann Sharpe +7.07 / DSR p≈0 / 全窓 positive = 探索全体で唯一の本物の edge**。だが net は GMO taker ~7bps で負（break-even 片道 ~1.5bps） |
| funding ⑤ | 単体 edge なし。tail-filter として v2 の pos_rate 69%→85% に上げる marginal+ |

### 再フレーム（本計画の核）

Round 1 の正味の結論は「アルファが無い」ではなく **「唯一の本物のアルファ（③）は既に見つかっており、残るのは執行コスト問題と銘柄ブレッドス不足」**である。

- ③ の net REJECT は **taker 7bps 前提**の評価。だが **GMO レバレッジ取引は per-trade 手数料ゼロ（日次ロールオーバのみ）**で、XS reversal の保有は分〜時間単位＝ロールオーバ負担は極小。現行 LIVE v2 も既に GMO レバレッジ・BOTH 方向で多銘柄ショートは射程内。→ **「taker で負」≠「GMO で取れない」。実効コストでの再測定が未踏。**
- XS 系は **3 銘柄（SOL/BTC/ETH）では狭すぎる**。breadth は XS edge の頑健性・キャパシティの前提。

→ 本計画は (A) ③ を実コストで monetize できるか、(B) 低頻度なら cost-tolerant な新 XS edge があるか、(D) funding filter で v2 を底上げ、(C) funding carry basket、の 4 トラックを EV/コスト順に検証する。

## 1. 再利用資産と制約

### 再利用できる基盤

- **Track③ マルチ資産ループ** [explore_track3_cross_sectional.py](../research/scripts/explore_track3_cross_sectional.py): SOL/BTC/ETH を共通タイムスタンプで整合し long-short basket を評価。`cost_bps` を turnover に課す片道コストモデル（L77/L109）。xs_momentum / xs_reversal / sol_buyhold / sol_tsmom を sweep 済み
- **fetch** [fetch_gmo_pair_15m_paced.py](../research/scripts/fetch_gmo_pair_15m_paced.py) / **resample** [resample_ohlcv.py](../research/scripts/resample_ohlcv.py)
- **execution model** [execution_model.py](../research/src/eval/execution_model.py): `slippage_bps` ベースの fill price。実効コストの sensitivity をここで表現
- **FundingGate** [regime_gates.py:446](../apps/gmo_bot/domain/strategy/components/regime_gates.py#L446)（Track⑤ で実装済み、Binance proxy CSV を lookahead 無しで参照）
- **Gate A/B/C 検証ハーネス** + 5層 component framework + 314 回帰テスト

### 制約

| 制約 | 箇所 | 影響 |
| --- | --- | --- |
| `_GMO_PAIRS` が 3 銘柄固定 | [source_registry.py:22](../research/src/data/source_registry.py#L22) | Track 0 で拡張が必要 |
| engine が単一資産・単一ポジション | `research/src/domain/backtest_engine.py` | XS 系（A/B）は **research 側ループ**で評価（engine の component-bundle allowlist には触れない）。LIVE 化時のみ execution 層の新規実装が前提 |
| funding/basis は GMO native に無い | API client 表面 | C は Binance proxy = perp venue 前提の PoC。D は外部 API 依存の執行系冗長化が要件 |

## Track 0: 銘柄ユニバース拡張（共通前提・小コスト）

A/B/C の breadth 前提。GMO の JPY 建てペア（XRP/LTC/BCH/XLM/ADA/DOT など流動性のある銘柄）を `fetch_gmo_pair_15m_paced` で 1y 取得し `research/data/raw/` に追加、`_GMO_PAIRS` を拡張する。

- **注意**: 上場時期が新しい銘柄は履歴が短く共通タイムスタンプ整合で評価窓を削る。流動性（spread）が薄い銘柄は XS の執行コストを悪化させるため、**流動性でユニバースをフィルタ**する
- Track A の最初の re-eval は**既存3銘柄のまま**実施可能（cost モデル変更のみ）。拡張は A が borderline 以上の手応えを示してから投資する（無駄打ち回避）

## Track A: XS reversal を GMO 実コストで再評価（最優先・最小コスト・最大天井）

**仮説**: ③ の net REJECT は taker 7bps 前提。GMO レバレッジ（per-trade 手数料ゼロ＋日次ロールオーバ）と maker/limit fill の実効コストなら、break-even（片道 ~1.5bps）を越えられる。

**手順**:

1. **実コストの確定**: GMO レバレッジ/現物の fee schedule と SOL/JPY の実 bid-ask spread を確認（ticker 板 or LIVE 約定ログから slippage を実測）。日次ロールオーバ率も確認
2. **Track③ loop の cost 再評価**: `--cost-bps` を {0.5, 1, 1.5, 2, 3} で sweep。さらに **maker-fill 比率モデル**（post-only で約定する割合 f、未約定は taker フォールバック or skip）を loop に追加
3. **ロールオーバ加算**: 保有時間に応じた日次手数料を turnover コストに上乗せ（短保有なので極小のはず＝検証）
4. `xs_rev_L4_H4` と近傍 (L,H) を net で評価。gross の強さ（Sharpe+7）がどの実効コストまで残るかの**フロンティアを引く**

**Done 基準**: 実効コスト前提で net rolling Sharpe が SOL buyhold を上回り、DSR p < 0.10、全窓 positive を維持。Gate A holdout `total_scaled_pnl_pct_ci_low > 0`

**撤退条件**: maker/レバレッジ実コストでも net 負 → ③ の結論を実コストで確定し、XS reversal は GMO では取れないと結論。低コスト外部 venue を持てた場合のみ再訪（本計画スコープ外）

## Track B: 低頻度 XS momentum（cost-tolerant 新系統）

**仮説**: 15m intraday では reversal が強く momentum は弱い（microstructure 由来）。だが **日次以上の horizon の cross-sectional momentum** は古典的に別系統で、回転が低く 7bps でもコスト耐性がある。Round 1 は 15m intraday しか見ておらず、低頻度 XS は未検証。

**手順**:

1. Track 0 で拡張したユニバースで rebalance を **4h / 12h / 日次**に上げ、lookback を数日〜数週で sweep（XS momentum と reversal 両方）
2. turnover が低いこと（gross に対しコストが小部分）を確認し、cost 7bps で net 評価
3. baseline は SOL buyhold / SOL tsmom / equal-weight basket

**Done 基準**: net rolling Sharpe が SOL buyhold を上回り DSR p < 0.10。turnover×cost が gross PnL の小部分（コスト耐性の確認）

**撤退条件**: 低頻度でも XS momentum が SOL buyhold 未満 → crypto XS momentum は SOL ユニバースでは取れない

## Track D: funding tail-filter を v2 LIVE へ（robustness 底上げ）

新エッジではなく既知の marginal+ filter を LIVE v2 の robustness 改善として正式投入する。

**既知の結果**（Round 1 ⑤ arm2）: `v2 + funding(high +0.0001 / low -0.0002)` で mean +0.23pt、**pos_rate 69%→85%（Phase2 基準 ≥80% を超える）**、min 不変、trades 398→388。

**手順**:

1. FundingGate を v2 config（composite gate に 1 つ追加）した variant で **Gate A holdout** を通す（CI low > 0 維持・pos_rate 改善の再現）
2. **外部 funding fetch の LIVE infra**: Binance fapi funding を定期取得するジョブ。**取得失敗時は filter 無効化＝v2 素通しの fail-open を既定**（外部 API 障害で取引停止しない）
3. **Gate B**: PAPER 30 日 + shadow_compare（trade 一致率 ≥ 95%）
4. **Gate C**: `position_size_multiplier = 0.5` で 30 日 → 本サイズ

**Done 基準**: Gate A holdout CI low > 0 維持 + pos_rate 改善が holdout で再現 + shadow 一致 ≥ 95%

**撤退条件**: 外部 API 依存の orchestration リスク（取得遅延・lookahead 混入）が +0.23pt の価値に見合わない → 見送り、v2 素のまま維持

## Track C: funding carry basket（PoC・perp venue 前提）

**仮説**: funding carry は本来 cross-sectional（多 perp の long 低funding / short 高funding）。SOL 単体（⑤ arm1）が弱くても **basket は別系統**。8h 回転＝コスト耐性。

**制約**: perp = Binance 等で GMO native 不可 → **research PoC に留め、monetize は venue 課題（LIVE 化は別判断）**。

**手順**:

1. Binance fapi で主要 perp（SOL/BTC/ETH + 他流動 perp）の funding(8h) + markPrice を取得（SOL は [sol_funding_binance_8h.csv](../research/data/raw/sol_funding_binance_8h.csv) にキャッシュ済）
2. long bottom-funding / short top-funding basket、8h rebalance、cost 7bps で評価
3. baseline は各単体 buyhold / equal-weight

**Done 基準**: basket の net ann Sharpe が各単体 buyhold を上回り DSR p < 0.10

**撤退条件**: basket でも DSR p > 0.10 → funding crowding は取れない（⑤ を basket に拡張しても結論不変）

## 推奨ロードマップ

最小コスト・最速 falsification 順:

1. **Track A step 1–2 を即実施**（既存3銘柄・実コスト再測定）— 最速の kill または最大の unlock。「proven edge が GMO 実コストで取れるか」を真っ先に確定
2. A が borderline 以上なら **Track 0 ユニバース拡張**に投資 → 拡張ユニバースで A を再評価し **Track B** も同じ loop で sweep
3. **Track D を並行**（A/B と独立、LIVE robustness）。外部 fetch infra の信頼性が主リスク
4. **Track C は PoC**（perp venue 判断待ち、最も live 距離が遠い）

## 撤退条件（早期 falsification）

| Track | 撤退条件 |
| --- | --- |
| A | maker/レバレッジ実コストでも net 負 → XS reversal は GMO では取れない |
| B | 低頻度でも XS momentum が SOL buyhold 未満 → crypto XS は SOL ユニバースで取れない |
| D | 外部 API 依存リスクが +0.23pt に見合わない → 見送り |
| C | basket でも DSR p > 0.10 → funding crowding は取れない |
| 全体 | A〜C 全滅 → 新規 edge 探索を凍結し現行 LIVE 維持のみ（D は独立に判断）。低コスト外部 venue を持てた時に A/C を再訪 |

## 検証ガード（全 Track 共通）

LIVE 投入判断は主観でなく Gate A/B/C（[README.md](../README.md) §Backtest validity gates）で行う。

- Gate A: holdout 主軸の CI / DSR / walk-forward / レジーム分解
- Gate B: PAPER 30 日 + shadow_compare（trade 一致率 ≥ 95%）
- Gate C: `position_size_multiplier = 0.5` で 30 日 → 本サイズ

**XS 系（A/B）の LIVE 化固有の前提**: engine が単一資産のため、LIVE には execution 層の新規実装（**マルチ資産の同時建玉・market-neutral な margin 管理・basket リバランス執行**）が必要。Gate B/C の前に execution PoC を別途要する。research での net edge 確認（Gate A 相当）が先決で、execution 投資はそれが通ってから。
