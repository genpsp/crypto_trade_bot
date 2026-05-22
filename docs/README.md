# crypto_trade_bot docs

このディレクトリは、GMO bot の戦略探索・設定変更で今後参照する記録だけを残す。

## 現在参照するドキュメント

- `runbook/gmo_v2_cutover.md` — v2 cutover / rollback / kill-switch / 週次評価手順。
- `gmo_bot_post_kill_phase2_findings.md` — 現行 v2 (`v2_dir_session_vol_time120`) 採用根拠。
- `gmo_bot_post_kill_postmortem_findings.md` — v2 の edge 発見につながった trade-level 事後分析。
- `gmo_bot_post_kill_phase1_findings.md` — pair/timeframe/trend 軸の低コスト反証結果。
- `gmo_bot_post_kill_phase3_v_findings.md` — mean reversion 失敗記録。再試行防止用。
- `gmo_bot_post_kill_exploration_plan.md` — post-kill 探索の判断基準・撤退条件。
- `gmo_bot_logic_exploration_plan.md` — component 化設計の前提。コードコメントから参照あり。
- `gmo_bot_logic_exploration_s4_findings.md` — S2〜S4 の総括と、探索インフラ/修正バグの記録。
- `baselines/gmo_ema_pullback_15m_both_v0__2026-05-16.md` — v0 baseline の固定基準点。

## 2026-05-22 に削除したもの

以下は後続ドキュメントに結論が集約済み、または実施済み plan のため削除した。
詳細が必要な場合は git history を参照。

- `gmo_bot_strategy_revision_plan.md`
- `gmo_bot_strategy_search_v1.md`
- `gmo_bot_strategy_search_v2.md`
- `gmo_bot_logic_exploration_s2_findings.md`
- `gmo_bot_logic_exploration_s3_findings.md`
