# gmo_bot エッジ探索 Round 3 計画（執行コスト monetize × 未踏データ次元）

- 対象: `apps/gmo_bot`（GMO コイン / SOL/JPY）+ research 側ループ + 外部データ screen
- 位置づけ: 新規エッジ探索は [gmo_bot_new_edge_findings.md](gmo_bot_new_edge_findings.md)（Round 1）/ [gmo_bot_edge_round2_findings.md](gmo_bot_edge_round2_findings.md)（Round 2）/ [altdata_edge_findings.md](altdata_edge_findings.md) で **2026-06 に一度凍結**（robust かつ net-tradeable な edge はゼロ）。本 Round は凍結を**限定的に解除**し、(1) 凍結結論が自ら指し示した唯一の「天井が高い」線＝執行コスト monetize、(2) 前回探索が**実際には触れていない**データ次元（板 microstructure / on-chain フロー / ニュース・センチメント）に絞って検証する
- 前提: 方向性アルファ（trend/MR/上位足/router/lead-lag/funding/マクロ）は枯渇確定。LIVE は `v2_dir_session_vol_time120` を維持
- 作成: 2026-06-06

## 0. 出発点（凍結の確定事実）と再フレーム

### Round 1–2 + alt-data の確定結論

| 領域 | 結論 |
| --- | --- |
| SOL/JPY 15m OHLCV ロジック組み替え | 枯渇・全 Gate 未達 |
| 上位足 / router / BTC lead-lag | REJECT（15m 固有・direction×hour と直交） |
| **cross-sectional short-term reversal** | **gross ann Sharpe +7.07 / DSR p≈0 / 全窓 positive = 探索全体で唯一の本物の edge**。だが +7 は GMO 低流動 JPY 板の bid-ask bounce 固有で流動 Binance では +0.76〜1.31、**net は全期間負**（break-even 片道 ~1.5bps vs GMO taker ~7bps） |
| funding carry / 集計funding | 相対funding(carry) は 2025-26 限定の period-specific（2.6年で消失）。集計funding 時系列は edge 無し |
| alt-data（マクロ/フロー/センチメント/野菜） | 見かけの IC は全て偽相関。ネガコン percentile＋循環シフト置換で例外なく棄却。「crypto を説明はするが日次で取引可能に予測はしない」 |

### 再フレーム（本計画の核）

凍結結論の正味は「アルファが無い」ではなく **「唯一の本物のアルファ（XS short-horizon reversal）は実在し、潰した原因は信号ではなく執行コスト」**。findings は明示的に「次に投資するなら低コスト執行経路が最も天井が高い」と書いている。

→ **板/microstructure 軸の最有用な使い道は「新しい方向性 alpha」ではない**。それは XS reversal と同じ net コスト壁に当たる既知の死に筋。**実在する edge を執行コスト削減で monetize する**ことが今回唯一の高天井ルートであり、最優先（Track A2）。

一方 on-chain / センチメントは alt-data 計画のスコープに名前はあったが **Track2(crypto構造) と STABLES 以外は実際には未実行**。未踏ではあるが、alt-data 全体の「日次×~5年では directional timing を頑健に検証できない」というデータ長の壁を共有するため、**filter/レジーム用途**として 1 回ずつ規律的に潰す（期待値は低い、無限 sweep には戻さない）。

## 1. 再利用資産と制約

### 再利用できる基盤

- **execution model** [execution_model.py](../research/src/eval/execution_model.py): fill price / slippage モデル。A2 の実効コスト・maker-fill 比率の sensitivity をここで表現
- **検証ハーネス** [statistics.py](../research/src/eval/statistics.py): `deflated_sharpe`（honest n_trials デフレート）/ `block_bootstrap_trades`+`bootstrap_ci` / `power_analysis`
- **Track③ マルチ資産ループ** [explore_track3_cross_sectional.py](../research/scripts/explore_track3_cross_sectional.py): XS basket 評価 + 片道 `cost_bps` モデル。A2 の net 再測定の母体
- **板収集** [collect_gmo_orderbook.py](../research/scripts/collect_gmo_orderbook.py): GMO SOL_JPY の spread/imbalance/depth + trades OFI を15秒間隔で前方収集
- **alt-data screen** [explore_altdata_screen.py](../research/scripts/explore_altdata_screen.py) / [fetch_crypto_structural.py](../research/scripts/fetch_crypto_structural.py) / 農産物ネガコン baseline / 循環シフト置換 [validate_stables_signal.py](../research/scripts/validate_stables_signal.py)・[validate_aggfund_signal.py](../research/scripts/validate_aggfund_signal.py)。B/C はここに統合
- **trial_ledger** [trial_ledger.csv](../research/data/altdata/trial_ledger.csv): honest n_trials 記録 → DSR デフレート
- **FundingGate** + 5層 component framework + 回帰テスト群

### 制約

| 制約 | 影響 |
| --- | --- |
| **板は前方収集のみ・履歴ゼロ** | 蓄積は現在 6/5 の約16分（66行）のみ。**収集プロセスは停止中**。A1 の統計的検証には継続収集 ≥4–6 週が必要 |
| engine が単一資産・単一ポジション | XS 系は research 側ループで評価。LIVE 化時のみ execution 層の新規実装が前提 |
| 日次×~5年のデータ長の壁 | B/C の directional timing は alt-data と同じく頑健検証が困難（ネガコンも OOS で勝つ）。**filter/レジーム用途に限定** |
| on-chain/funding は GMO native に無い | 外部 API（DefiLlama / Binance fapi 等）依存 = LIVE 化は執行系冗長化が前提 |
| lookahead 混入リスク（特に C） | point-in-time のみ。特徴は lag1、bar 時刻以前の最新値のみ参照 |

### 共通ゲート（全 Track 必須）

1. honest n_trials を trial_ledger に記録 → `deflated_sharpe` でデフレート
2. ネガティブコントロール（農産物 null / 信号シャッフル / 循環シフト置換）で偽陽性 baseline を測る
3. 複数窓 + **複数 venue（GMO / Binance）** で venue 固有性をチェック（XS reversal の教訓）
4. `execution_model.py` で**実コスト net** 評価（gross は信じない）
5. **Done（採用）/ 撤退条件をコード実行前に固定**（pre-registered）

## Track A2: 執行コスト削減で XS reversal を monetize（最優先・高天井）

**仮説**: XS reversal の net REJECT は taker ~7bps 前提。板（spread/imbalance/OFI）で entry をタイミングし maker 寄せすれば実効コストを break-even（片道 ~1.5bps）近くへ下げられ、実在 edge が net 黒転する。GMO レバレッジは per-trade 手数料ゼロ＋日次ロールオーバのみで、XS の短保有ならロールオーバ負担は極小。

**手順**:

1. **実コストの確定**: GMO SOL_JPY の実 bid-ask spread を収集済み板 CSV から実測（spread_bps の分布・時間帯依存）。レバレッジ fee schedule と日次ロールオーバ率も確認
2. **maker-fill 比率モデル**: post-only で約定する割合 f（板 imbalance/spread で条件付け）、未約定は taker フォールバック or skip。これを Track③ loop の cost 層に追加
3. **net フロンティア**: `xs_rev_L4_H4` と近傍 (L,H) を実効コスト {0.5, 1, 1.5, 2, 3} bps × maker-fill f で sweep。gross +7 がどの実効コストまで残るかのフロンティアを引く
4. 同じ実効コストモデルで **現行 v2** も再 net 化（v2 は GMO レバレッジ前提なので実コスト改善の恩恵を直接受ける）

**Done（採用）**: 現実的 maker 執行モデルで XS reversal か v2 の net rolling Sharpe が SOL buyhold（および現行 LIVE）を上回り、DSR p < 0.10、全窓 positive を維持。Gate A holdout `total_scaled_pnl_pct_ci_low > 0`

**撤退**: 達成可能実効コストが ~3bps を割れない（=③のコスト壁を越えられない）→ XS reversal は GMO では取れないと実コストで確定。低コスト外部 venue を持てた場合のみ再訪

## Track A1: 板 microstructure alpha（データ蓄積ゲート・A2 が手応えを示してから）

**仮説**: OFI / imbalance が次バー SOL 方向を予測する microstructure alpha。

**前提**: 履歴ゼロ＝**継続収集 ≥4–6 週が必須**。かつ XS reversal がまさに microstructure 由来で net コスト壁に当たった前例があるため、**A2 で執行コストの目処が立つまで本格収集には投資しない**（無駄打ち回避＝本 Round の意思決定どおり）。A2 が borderline 以上なら収集を自動再起動つきで常時化し、蓄積後に screen する。

**手順（A2 通過後）**:

1. 板 CSV を 15m バーに集約（OFI/imbalance の bar 内平均・終値）。lag1 で次バー return を予測
2. IC screen + **信号シャッフル置換**で偽陽性 baseline を測る。GMO / Binance 両板で符号一致をチェック（venue 固有性）
3. `execution_model.py` で実コスト net

**Done（採用）**: net DSR p < 0.05 かつ GMO/Binance 両 venue で符号一致 かつ実コスト net positive

**撤退**: gross は出ても net 負 or GMO 板固有（= XS reversal の再現）→ 即撤退

## Track B: on-chain / フロー（未踏・filter 用途・1 screen のみ）

**仮説**: 取引所ネットフロー・stablecoin 供給・大口移動が日次レジーム/方向に効く。STABLES は探索済み REJECT だが、取引所フロー・大口移動は未踏。

**手順**: DefiLlama / 取引所フロー API（日次・多年）を取得し既存 alt-data screen に統合。農産物 null baseline(偽陽性率 3%) と循環シフト置換で判定。STABLES と同じ土俵で **filter/レジーム**として評価。

**Done（採用）**: null percentile > 95% かつ循環シフト置換 p < 0.05 かつ OOS で符号維持

**撤退**: 農産物 null と区別不能（STABLES の前例）→ 撤退

## Track C: ニュース / センチメント（最も未踏・最も期待値低・1 screen のみ）

**仮説**: Fear&Greed 指数 / funding-as-sentiment 等が方向 or レジームを予測。

**前提**: alt-data 全体テーマ「説明はするが予測しない」＋データ長の壁＋ lookahead リスク（同時性リーク）が最も厳しい。**point-in-time の系列のみ**（F&G 多年日次・funding）を使い、安価に 1 回潰す。

**手順**: 同 screen に統合、農産物 null・循環シフト置換で判定。lookahead 厳格管理（lag1・bar 時刻以前の最新値のみ）。

**Done（採用）**: null percentile > 95% かつ置換 p < 0.05 かつ OOS 符号維持

**撤退**: 同時説明にとどまり予測せず → 撤退

## 推奨ロードマップ（最速 falsification 順）

1. **Track A2 を即実施**（既存板 CSV + 既存 v2/XS trades + `execution_model.py`）— 新 alpha 不要、実在 edge の net 化を真っ先に確定。最速の kill または最大の unlock
2. A2 が borderline 以上 → **板収集を常時化**（自動再起動）し、蓄積後に **Track A1**
3. **Track B → C** を alt-data screen に各 1 本（安価・並行可・期待値は低い）
4. A2 が黒転 → Gate A/B/C と XS の LIVE 化（execution 層実装）へ。これが本 Round の本命 deliverable

## 撤退条件（pre-registered kill）

| Track | 撤退条件 |
| --- | --- |
| A2 | 達成可能実効コストが ~3bps を割れない → XS は GMO では取れないと実コストで確定 |
| A1 | net 負 or GMO 板固有 → microstructure alpha は取れない |
| B | 農産物 null と区別不能 → on-chain フローは方向を当てない |
| C | 同時説明にとどまる → センチメントは予測しない |
| **全体** | **A2 が net 黒転せず、A1/B/C も null を越えなければ 3 軸とも凍結に戻し板収集を停止**。[project_crypto_bot_edge_search_frozen 系] 凍結結論を更新。低コスト外部 venue を持てた時に A2 を再訪 |

## 検証ガード（全 Track 共通）

LIVE 投入判断は Gate A/B/C（[README.md](../README.md) §Backtest validity gates）で行う。

- Gate A: holdout 主軸の CI / DSR / walk-forward / レジーム分解
- Gate B: PAPER 30 日 + shadow_compare（trade 一致率 ≥ 95%）
- Gate C: `position_size_multiplier = 0.5` で 30 日 → 本サイズ

**XS 系（A2/A1）の LIVE 化固有の前提**: engine が単一資産のため、LIVE にはマルチ資産の同時建玉・market-neutral margin 管理・basket リバランス執行の新規実装が必要。research での net edge 確認（Gate A 相当）が先決で、execution 投資はそれが通ってから。
