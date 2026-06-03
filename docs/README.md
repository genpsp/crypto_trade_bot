# crypto_trade_bot docs

このディレクトリは、GMO bot の戦略探索・設定変更で今後参照する記録だけを残す。

## 現在参照するドキュメント

- `gmo_bot_exploration_findings.md` — **探索結果サマリ**。現行 v2 (`v2_dir_session_vol_time120`) 採用根拠、Phase 1/2/3-V・post-mortem の結論、棄却した系統（再試行防止）、永続資産・修正バグを 1 本に集約。
- `gmo_bot_new_edge_exploration_plan.md` — **Round 1**（上位足/レジーム切替/クロスセクション/新データ次元）。①②④⑤ REJECT・③は gross edge ありで net コスト壁。結果は `gmo_bot_new_edge_findings.md`。
- `gmo_bot_edge_round2_exploration_plan.md` — **Round 2**。軸を「ロジック組み替え」から「執行コスト×ブレッドス」へ移し、③ XS reversal の GMO 実コスト再評価 / 低頻度 XS momentum / funding filter / funding carry を検証する計画。
- `altdata_edge_exploration_plan.md` — **外部データ Round**。OHLCV/funding 以外のあらゆる外部データ（他先物・マクロ・フロー・センチメント・野菜価格まで）で暗号資産の日次方向/レジームを予測する計画。背骨は反データマイニング規律（honest DSR・3分割・野菜価格等の無機序系列をネガティブコントロール=偽陽性率較正に使用）。
- `altdata_edge_findings.md` — 外部データ Round 結果。Track1/3/4 実施。ネガコン較正が機能（最強 |IC| は KO=コカ・コーラの偽陽性）。マクロ(equity risk-on)は真null 3% に対し 14% で本物の予測情報を持つが standalone tradeable edge にならず。Track2(crypto構造)は IS 有意率 33% で最高だが DVOL は OOS 符号反転、STABLES が一見有望だったが、候補1+2 の proper 検証（ネガコン percentile＋循環シフト置換）で STABLES(50%ile)・AGGFUND(置換 p~0.5) とも null と区別不能＝REJECT。alt-data の見かけ上の edge は全て偽陽性と判明。結論: tradeable な crypto edge は構造系(cross-sectional funding carry/XS)に限られ、外部データは crypto を説明するが日次で取引可能に予測しない。重要対比: 相対funding(C)は real だが集計funding の時系列タイミングは edge 無し。
- `gmo_bot_edge_round2_findings.md` — Round 2 結果。A(XS reversal 実コスト)=REJECT、B(低頻度 XS momentum＋拡張)=REJECT、D(funding filter)=見送り、C(funding carry)=候補3 で 2.6年に拡張すると out-of-sample 失敗（2025-26 限定、前半 2023-24 は -0.26）。③ XS reversal も長期再検証=gross は全期間持続するが +7 は GMO 低流動板の bid-ask bounce 固有で流動 Binance では +0.76〜1.31、net は全期間負。**最終結論: robust かつ net-tradeable な新規 edge はゼロ（③/C/alt-data の見かけの edge は全て venue 固有・期間固有・偽相関で proper 検証で消失）。plan 撤退条件に該当＝新規探索を凍結し現行 GMO LIVE v2 維持のみ。**
- `gmo_bot_logic_exploration_plan.md` — 5層 component framework の設計記録（§2）。コードコメントから参照。
- `dex_bot_v2_port_plan.md` — v2 戦略フレームワークを dex_bot へ移植する手順書。
- `runbook/gmo_v2_cutover.md` — v2 cutover / rollback / kill-switch / 週次評価手順。
- `baselines/gmo_ema_pullback_15m_both_v0__2026-05-16.md` — v0 baseline の固定基準点（`compare_runs` の左辺）。

## 整理履歴（詳細は git history を参照）

- **2026-05-30**: findings 5 本（post_kill phase1/phase2/phase3-v/postmortem + logic exploration s4）を `gmo_bot_exploration_findings.md` に統合。実行・上位互換済みの `gmo_bot_post_kill_exploration_plan.md` を削除。`gmo_bot_logic_exploration_plan.md` は歴史的記録を削り §2 component 設計のみに圧縮（26KB→約4KB）。
- **2026-05-22**: `gmo_bot_strategy_revision_plan.md` / `gmo_bot_strategy_search_v1.md` / `gmo_bot_strategy_search_v2.md` / `gmo_bot_logic_exploration_s2_findings.md` / `gmo_bot_logic_exploration_s3_findings.md` を削除（後続ドキュメントに結論集約済み）。
