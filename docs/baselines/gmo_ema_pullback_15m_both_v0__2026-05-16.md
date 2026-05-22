# Baseline: gmo_ema_pullback_15m_both_v0 @ 2026-05-16

戦略改修PRが `compare_runs --runs BASELINE,NEW` の左辺として参照する固定基準点。
本ファイルが指す run_id は、上書きせず別ファイル（新baseline）として更新する。

## 識別子

- **run_id**: `20260516-075500-gmo_15m_baseline_sensitivity-42119a6`
- **path**: `research/data/runs/20260516-075500-gmo_15m_baseline_sensitivity-42119a6/`
- **spec**: `research/sweeps/gmo_15m_baseline_sensitivity.yaml`
- **base config**: `research/models/gmo_ema_pullback_15m_both_v0/config/current.json`
- **git sha (at run time)**: `42119a638e475df9d5027f3fb3bf8f3b81cfe588`
- **dataset**: `research/data/raw/soljpy_15m_1y.csv` (2025-02-20 〜 2026-03-11, 35,041 bars)
- **holdout split**: train ≤ 2025-11-30 23:59:59Z / test ≥ 2025-12-01 00:00:00Z
- **execution model**: `stochastic_v1` (profile 未生成、`additional_slippage_bps=1.0`, seeds 1..5)
- **axes**: `risk.max_trades_per_day {2,3,4}` × `risk.volatile_size_multiplier {0.4,0.55,0.7}` × `exit.take_profit_r_multiple {1.6,1.8,2.0}`、full_grid 27ケース × 5 seed × 2 window = **270 trial**

## サマリ数値（seed均し後の代表ケース）

### TRAIN（2025-02-20 〜 2025-11-30）

| TP_R | MTPD | trades | WR | scaled PnL | CI low | return/DD | PF | avg R | DSR p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.0 | 4 | 378 | 39.15% | +60.89% | +9.93% | 3.82 | 1.23 | 0.12 | 0.776 |
| 1.6 | 4 | 423 | 44.44% | +56.69% | +1.41% | 3.30 | 1.21 | 0.11 | 0.783 |
| 2.0 | 2 | 350 | 39.14% | +55.55% | -6.85% | 3.96 | 1.23 | 0.12 | 0.799 |

### HOLDOUT（2025-12-01 〜 2026-03-11）

| TP_R | MTPD | trades | WR | scaled PnL | CI low | return/DD | PF | avg R | DSR p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.0 | 4 | 118 | 31.36% | **-8.14%** | -49.19% | -0.47 | 0.90 | -0.11 | 0.999 |
| 1.8 | 4 | 130 | 33.85% | -9.49%  | -36.52% | -0.66 | 0.89 | -0.10 | 0.999 |
| 1.6 | 4 | 140 | 35.71% | -12.48% | -43.40% | -0.68 | 0.87 | -0.12 | 0.999 |

### Gate A

| 判定 | trial数 |
| --- | --- |
| PASS | 0 |
| FAIL | 270 |

主な不通過項目: `holdout_pnl_ci_positive` / `return_to_dd_ci_positive` / `walk_forward_positive_ratio` / `all_trend_regimes_positive` / `deflated_sharpe_p_value` / `stochastic_seed_p05_ci_positive`

### regime 別（holdout, TP=2.0 / MTPD=4 / seed=1）

| 軸 | bucket | trades | WR | total PnL% | avg R |
| --- | --- | --- | --- | --- | --- |
| trend | BULL | 54 | 31.5% | -14.02% | -0.23 |
| trend | BEAR | 55 | 34.5% | -5.84% | -0.15 |
| trend | CHOPPY | 17 | 47.1% | +4.98% | +0.17 |
| vol | HIGH_VOL | 17 | 29.4% | -5.11% | -0.27 |
| vol | MID_VOL | 40 | 37.5% | -4.50% | -0.06 |
| vol | LOW_VOL | 69 | 34.8% | -5.26% | -0.15 |
| btc | RISK_ON | 71 | 35.2% | -9.04% | -0.13 |
| btc | RISK_OFF | 55 | 34.5% | -5.84% | -0.15 |

### 死に軸

- `risk.volatile_size_multiplier`: 27ケース全てで結果同値（`position_size_multiplier_counts = {"1.0": ...}` のみ） → VOLATILE/STORM regime が一度も発火していない。

## 関連 LIVE 結果（同じ判断材料として保管）

- `reports/2026-05-15_gmo_ema_pullback_15m_both_v0.html` (2026-04-15 〜 05-15, 36 trades / WR 27.78% / Net ¥-872 / PF 0.67 / SHORT 0勝8敗)
- `reports/2026-05-15_gmo_ema_pullback_2h_long_v0.html` (同期間 0 trade)
- `reports/2026-05-15_gmo_storm_2h_short_v0.html` (同期間 0 trade)

## 用途

1. **改修 PR の前後比較**
   ```bash
   python -m research.scripts.compare_runs \
     --runs 20260516-075500-gmo_15m_baseline_sensitivity-42119a6,<NEW_RUN_ID> \
     --metric return_to_dd
   ```
   出力を PR description に貼ること。

2. **Gate A 再評価**: 改修後 run で `compare_runs --run latest --gate-a` の PASS 行が >=1 になるかを判定。

3. **方針ドキュメント参照**: 旧 `gmo_bot_strategy_revision_plan.md` は 2026-05-22 に整理済み。数値根拠はすべてこの baseline run に集約。

## 再現手順

```bash
git checkout 42119a638e475df9d5027f3fb3bf8f3b81cfe588
python -m research.scripts.run_sweep \
  --spec research/sweeps/gmo_15m_baseline_sensitivity.yaml \
  --workers 4
# 同じ trial 数 / 同じ summary が再生成されること
```

## 後継 baseline を作るタイミング

- データ拡張（`data_sync` で 2024-01-01〜 を取り込み済み）したら新 baseline を切る
- sweep spec が変わったら（軸を差し替えた `gmo_15m_diagnosis_v1.yaml` 等）新 baseline を切る
- いずれも本ファイルは触らず、`docs/baselines/{model}__{YYYY-MM-DD}.md` を別ファイルで追加する
