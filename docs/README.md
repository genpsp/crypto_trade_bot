# crypto_trade_bot docs

このディレクトリは、GMO bot の戦略探索・設定変更で今後参照する記録だけを残す。

## 現在参照するドキュメント

- `gmo_bot_exploration_findings.md` — **探索結果サマリ**。現行 v2 (`v2_dir_session_vol_time120`) 採用根拠、Phase 1/2/3-V・post-mortem の結論、棄却した系統（再試行防止）、永続資産・修正バグを 1 本に集約。
- `gmo_bot_new_edge_exploration_plan.md` — LIVE 運用中の追加エッジ探索計画（上位足/レジーム切替/クロスセクション/新データ次元）。
- `gmo_bot_logic_exploration_plan.md` — 5層 component framework の設計記録（§2）。コードコメントから参照。
- `dex_bot_v2_port_plan.md` — v2 戦略フレームワークを dex_bot へ移植する手順書。
- `runbook/gmo_v2_cutover.md` — v2 cutover / rollback / kill-switch / 週次評価手順。
- `baselines/gmo_ema_pullback_15m_both_v0__2026-05-16.md` — v0 baseline の固定基準点（`compare_runs` の左辺）。

## 整理履歴（詳細は git history を参照）

- **2026-05-30**: findings 5 本（post_kill phase1/phase2/phase3-v/postmortem + logic exploration s4）を `gmo_bot_exploration_findings.md` に統合。実行・上位互換済みの `gmo_bot_post_kill_exploration_plan.md` を削除。`gmo_bot_logic_exploration_plan.md` は歴史的記録を削り §2 component 設計のみに圧縮（26KB→約4KB）。
- **2026-05-22**: `gmo_bot_strategy_revision_plan.md` / `gmo_bot_strategy_search_v1.md` / `gmo_bot_strategy_search_v2.md` / `gmo_bot_logic_exploration_s2_findings.md` / `gmo_bot_logic_exploration_s3_findings.md` を削除（後続ドキュメントに結論集約済み）。
