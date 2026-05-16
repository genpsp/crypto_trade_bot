# gmo_bot 戦略方向修正プラン

- 対象: `apps/gmo_bot`（GMOコイン SOL/JPY レバレッジ前提）
- 主要モデル: `gmo_ema_pullback_15m_both_v0`（`storm_2h_short_v0` / `ema_pullback_2h_long_v0` はLIVEで売買発生なし）
- バックテスト run: `research/data/runs/20260516-075500-gmo_15m_baseline_sensitivity-42119a6`
  - spec: `research/sweeps/gmo_15m_baseline_sensitivity.yaml`
  - データ: `research/data/raw/soljpy_15m_1y.csv`（2025-02-20 〜 2026-03-11）
  - holdout 分割: train 2025-02-20→2025-11-30、test 2025-12-01→2026-03-11
  - axes: `risk.max_trades_per_day {2,3,4}` × `risk.volatile_size_multiplier {0.4,0.55,0.7}` × `exit.take_profit_r_multiple {1.6,1.8,2.0}`、stochastic seeds 5本、計 270 trial（27パラメータ × 2 window × 5 seed）

## 0. 結論（先に）

現行 `gmo_ema_pullback_15m_both_v0` は **採用候補に値しない**。

- Gate A: **270 trial すべて FAIL**（holdout の CI 下限、return/DD、deflated Sharpe、walk-forward が全条件 NG）
- holdout（直近 3.3ヶ月）は **どの27パラメータでも PnL がマイナス**（-8% 〜 -16% scaled）
- LIVE 直近30日（2026-04-15 〜 05-15）: 36 trades / WR 27.8% / Net -872 JPY / PF 0.67、SHORT は **0勝8敗**
- `risk.volatile_size_multiplier` は **27ケース全てで結果が同値** → 該当データ区間では VOLATILE/STORM regime が一度も発火していない（`position_size_multiplier_counts={"1.0": ...}` のみ）。チューニング軸として死んでいる。
- LIVE / holdout / train を通じて唯一安定して赤の構造的要因は **SHORT 側の歪み**（短期 reclaim → swing high stop の R:R が成立せず、4h ドリフトガードを通った後でも勝率0%）

最優先で「LIVE を止める / 計測軸を直す / 戦略の前提を見直す」の3層に分けて段階的に修正する。

---

## 1. バックテスト結果ダイジェスト

### 1.1 LIVE 30日（2026-04-15 〜 2026-05-15、`reports/2026-05-15_gmo_ema_pullback_15m_both_v0.html`）

| 指標 | 値 |
| --- | --- |
| Total / Closed | 36 / 36 |
| Win rate | 27.78% |
| Net PnL | ¥-872（fees ¥232） |
| Profit factor | 0.67 |
| Avg R:R | 1.75 |
| Max drawdown | ¥-1,243（-206.14%） |
| Longest loss streak | 8 |
| Sharpe (ann.) | -3.33 |

- 方向別: LONG 28本（WR 35.7% / +¥17）/ SHORT 8本（WR **0%** / -¥657）
- 終了理由: STOP_LOSS 26本（全敗）/ TAKE_PROFIT 9本（全勝）/ MANUAL 1本
- 3連敗以上クラスタが期間中5回（最大8連敗）
- 期間中の運用上の問題: 同期間に `GMO executions payload invalid` の FAILED が大量発生（2026-05-11 朝、20件超）、`MAINTENANCE` 22回、`Requests are too many` 1回。意思決定品質とは別軸で `gmo_bot` の I/O 信頼性課題がある（→ 5章で別タスク化）

### 1.2 TRAIN（2025-02-20 〜 2025-11-30、約9ヶ月）

代表4ケース（seed均し後）。

| TP_R | MTPD | vsm | trades | WR | scaled PnL | CI low | return/DD | PF | avg R | DSR p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.0 | 4 | (any) | 378 | 39.15% | +60.89% | **+9.93%** | 3.82 | 1.23 | 0.12 | 0.776 |
| 2.0 | 3 | (any) | 374 | 39.04% | +57.96% | -2.34% | 3.83 | 1.22 | 0.12 | 0.799 |
| 1.6 | 4 | (any) | 423 | 44.44% | +56.69% | +1.41% | 3.30 | 1.21 | 0.11 | 0.783 |
| 1.6 | 2 | (any) | 370 | 44.86% | +52.91% | -3.93% | 3.64 | 1.22 | 0.11 | 0.780 |

- 27ケース中 **CI 下限が正なのは2ケースのみ**（TP=2.0/MTPD=4、TP=1.6/MTPD=4 系）
- avg R-multiple は最高でも 0.12（薄い優位性、損益分岐ぎりぎり）
- DSR p-value 0.77〜0.93 → train 単体でも統計的有意ではない
- TRAIN を後半半分に絞ると `second_half_scaled_pnl_pct` が大きく劣化するケースが多数（最近ほどエッジが減衰）

### 1.3 HOLDOUT（2025-12-01 〜 2026-03-11、約3.3ヶ月）

| TP_R | MTPD | vsm | trades | WR | scaled PnL | CI low | return/DD | PF | avg R | DSR p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.0 | 4 | (any) | 118 | 31.36% | **-8.14%** | -49.19% | -0.47 | 0.90 | -0.11 | 0.999 |
| 1.8 | 4 | (any) | 130 | 33.85% | -9.49% | -36.52% | -0.66 | 0.89 | -0.10 | 0.999 |
| 1.6 | 4 | (any) | 140 | 35.71% | -12.48% | -43.40% | -0.68 | 0.87 | -0.12 | 0.999 |
| 1.8 | 2 | (any) | 123 | 32.52% | **-16.44%** | -46.87% | -0.89 | 0.81 | -0.14 | 0.999 |

- 27ケース **全てマイナス**、return/DD は -0.47 〜 -0.89
- DSR p-value ≈ 0.999（エッジ無し）
- LIVE 30日（WR 27.78%）と同じ方向（むしろ holdout より悪い）に劣化が進行中

### 1.4 regime 別（最良ケース TP=2.0/MTPD=4 の seed=1）

| 軸 | bucket | trades | WR | total PnL% | avg R |
| --- | --- | --- | --- | --- | --- |
| trend | BULL | 54 | 31.5% | **-14.02%** | -0.23 |
| trend | BEAR | 55 | 34.5% | -5.84% | -0.15 |
| trend | CHOPPY | 17 | 47.1% | +4.98% | +0.17 |
| volatility | HIGH_VOL | 17 | 29.4% | -5.11% | -0.27 |
| volatility | MID_VOL | 40 | 37.5% | -4.50% | -0.06 |
| volatility | LOW_VOL | 69 | 34.8% | -5.26% | -0.15 |
| btc_corr | RISK_ON | 71 | 35.2% | -9.04% | -0.13 |
| btc_corr | RISK_OFF | 55 | 34.5% | -5.84% | -0.15 |

- **トレンドフォロー戦略のはずが BULL で最悪**（-14%）。本来の前提が崩れている。
- CHOPPY だけ唯一+5% だが標本17で信頼区間外。
- LOW_VOL が最大頻度（69本/126本）かつ赤字 → ATR 低位帯でだけ無理にエントリーしている。

### 1.5 NO_SIGNAL 内訳（holdout）

| reason | 件数 |
| --- | --- |
| MAX_TRADES_PER_DAY_REACHED | 2348 |
| EMA_SHORT_TREND_FILTER_FAILED | 1281 |
| EMA_TREND_FILTER_FAILED | 858 |
| SHORT_UPPER_CLOSE_DRIFT_TOO_POSITIVE | 555 |
| UPPER_TREND_EMA_NOT_STABLE | 482 |
| SHORT_BREAKDOWN_NOT_CONFIRMED | 479 |
| SHORT_RECLAIM_NOT_FOUND | 466 |
| SHORT_PULLBACK_NOT_FOUND | 204 |
| LONG_ATR_REGIME_TOO_HOT | 19 |
| LONG_WEAK_UPPER_TREND_NOT_CONFIRMED_BY_2H | 12 |

- SHORT 側ガード（DRIFT_TOO_POSITIVE / BREAKDOWN_NOT_CONFIRMED / RECLAIM_NOT_FOUND）で 1500件以上を弾いた**にもかかわらず** LIVE/HOLDOUT で SHORT は勝てていない。ガードの方向は正しいが、通過後の質が悪い（＝SHORTの前提自体に欠陥）。
- `MAX_TRADES_PER_DAY_REACHED` が最多 → 既にエントリー上限で削っており、cadence 緩和でエッジが回復する可能性は薄い。

### 1.6 2h モデル

- `ema_pullback_2h_long_v0` / `storm_2h_short_v0` ともに LIVE 30日で **trade 0**。
  - シグナル条件が GMO SOL/JPY 環境では発火しない／direction 制約と上位足ガードの組合せで `signal_timeframe=2h` がほぼ通らない。
  - 一度バックテストで「そもそも何本シグナルが出るのか」を確かめる必要がある。現状リソースを割く優先度は低い（→ 5.3 後送り）。

---

## 2. 重要所見（修正方向に直結するもの）

1. **戦略は出口で勝てない**: train でも avg R 0.12、holdout で -0.11。固定 TP=1.8R / swing-low stop の R:R 設計が SOL/JPY 15m の値動きに合っていない。
2. **SHORT は壊れている**: LIVE 0/8、holdout も 27 ケース全敗。`storm_short_v0` 系の SHORT 専用ロジックとは別の SHORT 経路が混在し、4h ドリフトガードを通った後の reclaim / breakdown 構造が SOL/JPY では反転シグナルになっている可能性が高い。
3. **`volatile_size_multiplier` は死に軸**: 270 trial 全てで `position_size_multiplier_counts={"1.0": ...}` のみ。`volatile_atr_pct_threshold=0.9` / `storm_atr_pct_threshold=1.4` が直近 1年の ATR% 分布の上に置かれている。
4. **train → holdout で全数値が崩壊**: scaled PnL +60% → -8%、WR 39% → 31%、avg R 0.12 → -0.11、DSR p 0.78 → 0.999。明確な overfit または regime shift。
5. **本来優位なはずの BULL trend regime で大負け**: トレンドフォロー枠組み自体に疑問。エントリータイミング（pullback → reclaim）がトレンド継続局面で逆行している。
6. **LIVE 期間に I/O 障害**: GMO executions payload invalid 連発・MAINTENANCE 22回。戦略以前にイベント側で `FAILED` が積み上がる構造があるので、戦略修正と並行で観測網を強化する。

---

## 3. 方向修正方針（優先度順）

### P0. 戦略 LIVE を一旦縮退する

- `gmo_ema_pullback_15m_both_v0` の **`direction` を BOTH → LONG に切替**（SHORT 側を即時停止）
- `position_size_multiplier` を 0.5 に絞る（README §Gate C 相当）
- これは「現行戦略の救済」ではなく、後段の再評価まで損失拡大を止めるための一時措置
- 実施場所: Firestore `models/gmo_ema_pullback_15m_both_v0` の `direction` / `models/.../config/current` の `position_size_multiplier`
- 2h モデルは現状 trade=0 のため触らない

### P1. 計測軸の修正（バックテスト spec の刷新）

現行 sweep は分解能ゼロの軸（vsm）を含み、本質的な悪化要因を捕まえられていない。次の構成に差し替える。

新 spec 案（`research/sweeps/gmo_15m_diagnosis_v1.yaml`）の差分骨子:

```yaml
axes:
  # 1) vsm は廃止、代わりに volatility threshold を動かす
  - path: risk.volatile_atr_pct_threshold
    values: [0.45, 0.6, 0.75, 0.9]
  - path: risk.storm_atr_pct_threshold
    values: [0.9, 1.1, 1.4]
  # 2) 方向を切り分けて評価
  - path: direction
    values: ["LONG", "SHORT"]
  # 3) R:R を再探索（TP 上げと stop タイトの両方向）
  - path: exit.take_profit_r_multiple
    values: [1.4, 1.8, 2.2, 2.6]
  - path: strategy.atr_stop_multiplier
    values: [1.2, 1.5, 1.8]
```

- `combinations: full_grid` は廃し `latin_hypercube` または手動の候補リストに変更（trial 爆発回避）
- holdout 区間は維持しつつ、**walk-forward window を 90/30/30 に短縮**して直近データへの追従性を測る
- `--keep-trades all` を常用し、`research/notebooks/trial_drilldown.ipynb` で BULL 局面の負け方を必ず可視化

### P2. データ拡張と最新化

- 現 raw データは 2026-03-11 まで。**`research.scripts.data_sync` で 2026-05-15 まで延伸**して、LIVE 期間と完全に重なる shadow backtest を成立させる。
  ```
  python -m research.scripts.data_sync --broker GMO_COIN --pair SOL/JPY --timeframe 15m --since 2024-01-01
  ```
- 2024年通年も取り込み、train の標本を1年→2年に拡張（DSR p を下げるための母数確保）
- `research/data/execution_profiles/gmo_soljpy.json` を **LIVE 36 trade から build** して `build_execution_profile` で生成。現状 stochastic_v1 が profile 無しで動いており、滑り推定が現実離れしている可能性がある。

### P3. ロジック改修候補（仮説駆動）

P1 / P2 で「どの軸が効くか」が判った後に着手する。今は仮説のみ列挙。

1. **SHORT 経路の分離・厳格化**: `gmo_ema_pullback_15m_both_v0` の SHORT を切り出し、`storm_short_v0` と整合した「breakdown 確認＋ 4h downtrend 持続」を専用モデルに移管。BOTH モデルでは SHORT を扱わない。
2. **LOW_VOL カットオフ**: holdout で LOW_VOL=69 trade/-0.15 avg R。`long_atr_pct_min` を導入し、`atr_pct < 0.30` 帯のエントリーを停止。
3. **BULL での負けパターン特定**: drilldown で「reclaim 後に再度割った場合の連敗」が支配的なら、`max_distance_from_ema_fast_pct` のさらなる縮小（0.9 → 0.6）、または `pullback_lookback_bars` 短縮（6 → 3〜4）。
4. **R:R 再設計**: avg R が train でも 0.12 しかない＝ TP 到達前に SL されているケースが多い。`atr_stop_multiplier` を 1.5 → 1.2 にしてストップを浅くし、TP を 2.2〜2.6R に伸ばす方向で「勝率は落ちるが R:R で稼ぐ」形を試す。
5. **regime gate の追加**: BULL/BEAR 別の WR と avg R をベースに、`upper_trend_gap_pct` の最小ラインを上げる（現 LONG 弱トレンド条件は `0.05%` と緩い）。

### P4. Gate 設計の再運用

- README §Gate A の閾値は妥当。**現状 0/270 PASS の状況を「期待値」として受け止め**、改修後に 1ケースでも PASS した時点で初めて Gate B（PAPER 30日）へ進める。
- Gate B の `shadow_compare` を回すために、LIVE 36 trade の JSON エクスポート整備（`apps/gmo_bot/app/reporting` か `scripts/` に CLI を追加）が前提タスクになる。

### P5. 運用基盤の補修（戦略とは独立）

- `GMO executions payload invalid` の頻発: `apps/gmo_bot/adapters/execution` の executions レスポンス検証で `status=4 / status=5` 系のリトライ / バックオフを再点検。LIVE で20分間連続 FAILED していたインシデントは戦略改修の前提条件として塞ぐ。
- `lock:runner already acquired` 1213回は分秒スケジューラの正常動作だがレポートのノイズなので、`reports.py` の SKIPPED 集計から除外条件を追加。

---

## 4. 実行ロードマップ

| 順 | タスク | 出力 | 担当箇所 | Gate |
| --- | --- | --- | --- | --- |
| 1 | LIVE 縮退（SHORT 停止 / size 0.5） | Firestore 設定変更ログ | Firestore + Slack 通知 | 即日 |
| 2 | data_sync で 2024-01〜2026-05-15 まで Parquet 化 | `research/data/cache/...` | `research.scripts.data_sync` | データ存在確認 |
| 3 | execution_profile 構築 | `research/data/execution_profiles/gmo_soljpy.json` | `build_execution_profile` | profile JSON |
| 4 | 新 sweep spec 作成 & 実行 | `research/data/runs/{date}-gmo_15m_diagnosis_v1` | `run_sweep --keep-trades all` | trial 完走 |
| 5 | drilldown で BULL 連敗 / SHORT 連敗の構造抽出 | notebook 出力 | `trial_drilldown.ipynb` | 仮説確定 |
| 6 | P3 ロジック改修（1案ずつ） | PR + `compare_runs --runs A,B` | `apps/gmo_bot/domain/strategy/...` | regression diff 添付 |
| 7 | Gate A 評価 | `compare_runs --gate-a` PASS 行が >=1 | — | PASS したら Gate B へ |
| 8 | Gate B（PAPER 30日 + shadow_compare） | shadow_compare レポート | `apps/gmo_bot` PAPER モード | 一致率95% / PnL偏差±20-30% |
| 9 | Gate C（LIVE 0.5x 30日） | LIVE レポート | LIVE | DD < backtest p95、reject 率 +50% 以内 |

### Definition of Done（戦略採用）

- Gate A: holdout 単体で `total_scaled_pnl_pct_ci_low > 0` かつ `return_to_dd_ci_low > 0`、DSR p < 0.05、BULL/BEAR/CHOPPY すべて positive、stochastic seed p05 でも CI 下限 positive
- Gate B: PAPER 30日で `shadow_compare` の trade 単位一致率 >= 95% / 累積 PnL 偏差 [-20%, +30%]
- Gate C: LIVE 30日で `max_drawdown` が backtest p95 を超えない / `execution_profile` 比 reject 率 +50% 以内

---

## 5. 次の1スプリントで踏むコマンド（実行レベル）

```bash
# 1. 一時停止（Firestore 経由、手動）
#    models/gmo_ema_pullback_15m_both_v0
#      direction: BOTH -> LONG
#    models/.../config/current
#      execution.position_size_multiplier: 1.0 -> 0.5

# 2. データ最新化（>= 2024-01）
python -m research.scripts.data_sync \
  --broker GMO_COIN --pair SOL/JPY --timeframe 15m \
  --since 2024-01-01

# 3. LIVE trade を JSON でダンプ → execution profile 構築
#    （ダンプ CLI は P5 の補修タスクで新設）
python -m research.scripts.build_execution_profile \
  --broker GMO_COIN --pair SOL/JPY \
  --input live_trades.json \
  --output research/data/execution_profiles/gmo_soljpy.json

# 4. 新 sweep（spec は P1 の骨子で別 PR にて追加）
python -m research.scripts.run_sweep \
  --spec research/sweeps/gmo_15m_diagnosis_v1.yaml \
  --workers 4 --keep-trades all

# 5. ranking / 軸別 / Gate A
python -m research.scripts.compare_runs --run latest --metric return_to_dd --top 20
python -m research.scripts.compare_runs --run latest --metric return_to_dd --marginal
python -m research.scripts.compare_runs --run latest --gate-a

# 6. 改修 PR ごとに前後比較
python -m research.scripts.compare_runs \
  --runs <RUN_BEFORE>,<RUN_AFTER> --metric return_to_dd
```

---

## 付録 A. 参照

- バックテスト原データ: `research/data/runs/20260516-075500-gmo_15m_baseline_sensitivity-42119a6/trials.parquet`
- LIVE レポート: `reports/2026-05-15_gmo_ema_pullback_15m_both_v0.html`
- 戦略ロジック: `apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py`
- 現行モデル設定: `research/models/gmo_ema_pullback_15m_both_v0/config/current.json`
- Gate 定義: `README.md` §Backtest validity gates
