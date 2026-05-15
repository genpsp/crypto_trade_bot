# research バックテスト 実践判断レベル化 指示書（Phase 5+）

姉妹文書: [research_backtest_platform.md](./research_backtest_platform.md)

---

## 1. 位置づけ

姉妹文書（platform）の Phase 1–4 で「比較・反復・再現性」の生産性問題は解決する。だがそれだけでは **「バックテストが良かったから LIVE に出して良い」とは言えない**。本書は Phase 5 以降として、**バックテスト結果を実運用の go/no-go 判断に使えるレベルに引き上げる**ための改修方針をまとめる。

姉妹文書がインフラ整備なら、こちらは **検証の妥当性** の話。両者は独立に進めて良いが、Phase 5+ は Phase 1–4 が前提（`runs/` ディレクトリ、`TrialResult` 構造、スウィープ YAML を活用する）。

---

## 2. 「実践判断に足る」の定義

本書では、以下を全て満たした状態を "実践判断に足る" と定義する:

1. **執行コスト・摩擦が現実と乖離していない** — backtest の `total_scaled_pnl_pct` と、同期間の LIVE 結果（あるいは paper trade）の差が、説明可能な範囲（trade 単位で 95% 一致 / 累積 PnL で ±N%）に収まる。
2. **数値の不確実性が定量化されている** — メトリクスは単一値ではなく信頼区間付きで提示され、スウィープ規模を考慮した補正値（Deflated Sharpe 等）が併記される。
3. **過去レジームの偏りが見えている** — bull / bear / choppy / high-vol / low-vol に分解した成績が表示され、ある1レジームだけで稼いでいるケースを識別できる。
4. **ルックアヘッドが機械的に否定されている** — シャッフルテスト・時間境界テストが CI で常時 PASS。
5. **採用ゲートが明文化されている** — backtest 結果 → paper forward test → 最小サイズ LIVE という多段ゲートが文書化され、各段の合否基準が数値で定義される。
6. **LIVE と backtest が日次で照合される** — LIVE の trade ログと、同設定・同データで再走した backtest が日次で diff され、乖離が閾値超過したら alert が出る。

---

## 3. 改修フェーズ一覧

| Phase | テーマ | 効くもの | 工数感 |
|---|---|---|---|
| 5 | 執行モデル現実化 | 期待 PnL の overestimate を是正 | 大 |
| 6 | 統計的厳密性 | selection bias / multiple testing | 中 |
| 7 | Forward test 整合 | LIVE-backtest 乖離検知 | 中 |
| 8 | データ代表性 / レジーム分解 | レジーム依存の過信を排除 | 中 |
| 9 | ルックアヘッド監査 | 致命的バグの早期検知 | 小 |
| 10 | 運用ガード整合 | backtest と LIVE の制御差を埋める | 小 |
| 11 | 採用ゲート明文化 | 主観排除の意思決定ルール | 小（運用） |

---

## 4. Phase 5: 執行モデルの現実化

### 4.1 現状の理想化点

[research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py) は次の前提に立つ。これらは LIVE の挙動と乖離する。

1. **対称 slippage 一定** — `_simulate_buy_fill_price` / `_simulate_sell_fill_price` で `slippage_bps` を固定加減算。実際は時間帯・ATR・スプレッド・ヒット強度で変動。
2. **stop 約定が `stop_price` で即完了** — gap で stop を飛び越えた時の最悪フィル（次バー open のさらに不利側）が表現されない。
3. **TP と stop 同バーは常に stop 優先**（既に pessimistic）— ここは現状のままで良い。**むしろこれを「規約」として明文化**し、`execution_model_id` で参照可能にする。
4. **エントリーは `decision.entry_price` で同バー約定** — `ON_BAR_CLOSE` 戦略は実際には「bar close 通知 → API 発注 → 次バー早期に約定」となる。バー close 価格そのものでの約定は楽観的。
5. **約定 reject / partial fill が無い** — DEX swap fail, GMO rate limit, 部分約定が表現されない。
6. **API レイテンシ無視** — close → 約定までの数秒〜十秒のラグで価格が動く影響が無い。

### 4.2 改修内容

#### 4.2.1 `ExecutionModel` 抽象の導入

```python
# research/src/eval/execution_model.py（新規）
class ExecutionModel(Protocol):
    model_id: str  # runs/manifest に記録される

    def simulate_entry_fill(self, *, decision, bars, index, rng) -> EntryFill | RejectedEntry: ...
    def simulate_stop_fill(self, *, position, bar, rng) -> ExitFill: ...
    def simulate_tp_fill(self, *, position, bar, rng) -> ExitFill: ...
    def simulate_same_bar_stop_and_tp(self, *, position, bar, rng) -> ExitFill: ...
```

実装は最低3種類:

- `IdealExecutionModel` — 現状の挙動。回帰テスト用。
- `PessimisticExecutionModel` — 決定論的に「悪い側」をフィル:
  - entry: 次バー open + 固定追加スリッページ
  - stop: `min(bar.open, stop_price)` を基準に追加スリッページ（gap 想定）
- `StochasticExecutionModel` — 乱数ドロー:
  - slippage を分布（normal / lognormal）からサンプリング
  - reject 確率 `p_reject` で trade スキップ
  - latency_seconds で entry を `next_bar.open` 側に寄せる
  - **fixed seed × N シードで反復**して `(mean, p05, p95)` を出す

[research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py) の固定 `_simulate_*_fill_price` を撤去し、`config["execution"]["model_id"]` から `ExecutionModel` をディスパッチする。

#### 4.2.2 LIVE 実績からのパラメータ推定

`apps/*/` の LIVE bot は trade ログを Firestore に書いている。これを使う:

1. **slippage_bps の分布推定**: LIVE の `expected_price` と `actual_fill_price` の差を `(direction, hour_of_day, atr_pct_bucket)` で層別化して保存。
2. **reject 率の推定**: 発注試行に対する成功率を broker 別に集計。
3. **latency 分布**: bar close 時刻 → fill 時刻のラグを記録。

これを `research/data/execution_profiles/{broker}_{pair}.json` に保存し、`StochasticExecutionModel` が読み込む。

```bash
python -m research.scripts.build_execution_profile \
  --broker GMO_COIN --pair SOL/JPY \
  --since 2025-06-01 \
  --output research/data/execution_profiles/gmo_soljpy.json
```

#### 4.2.3 スウィープ仕様への組込み

```yaml
execution_model:
  id: stochastic_v1
  profile: research/data/execution_profiles/gmo_soljpy.json
  seeds: [1, 2, 3, 4, 5]      # 各 trial を5シードで反復
```

`TrialResult.summary` に `seed` 次元を追加し、メトリクスは seed 集約（平均と p05/p95）で記録する。

#### 4.2.4 完了条件

- 既存 `IdealExecutionModel` で再走したスウィープ結果が、refactor 前と完全一致（回帰テスト）。
- `StochasticExecutionModel` で同じ spec を回した結果が、`IdealExecutionModel` 比で `total_scaled_pnl_pct` の中央値が下がる方向に動く（**過信補正の効きが目に見える**）。
- 過去30日の LIVE 実績と `StochasticExecutionModel` の backtest を `shadow_compare`（Phase 7）した時、平均 PnL 乖離が ±20% 以内。

---

## 5. Phase 6: 統計的厳密性

### 5.1 解決したい問題

- 100 ケース sweep のトップ1は **偶然である確率が支配的**。Bonferroni 換算で実効 p 値はトップ1の素朴 p 値の100倍。
- 単一の `return_to_dd` 数値で順位付け → 微差で2位を切る判断が現実には誤差の範囲。
- `closed_trades=8` のケースが `closed_trades=80` のケースと同列でランキングされる → ノイズ採用リスク。

### 5.2 改修内容

#### 5.2.1 Bootstrap 信頼区間

- `research/src/eval/statistics.py` 新規。
- `block_bootstrap_trades(trades, n_resamples=1000, block_size=10)` — トレード列をブロック単位でリサンプリング。連続トレードの依存性を保存。
- 各メトリクス（`total_scaled_pnl_pct`, `win_rate_pct`, `return_to_dd`, `average_r_multiple`）に `_ci_low`, `_ci_high`（95% 区間）を付与し `TrialResult.summary` に追加。
- ランキング表（`run_overview.ipynb`）は **CI 下限 ≤ 0 のケースを半透明化** する規約。「中央値で勝っているように見えても下限が負」を視覚的に弾く。

#### 5.2.2 Multiple testing 補正

- **Deflated Sharpe Ratio** (Bailey & López de Prado) を実装:
  - `deflated_sharpe(returns, n_trials, skew, kurt) -> (dsr, p_value)`
  - スウィープのケース数 `n_trials` を入力して、過剰最適化を罰した実効値を算出。
- ランキング表に `deflated_sharpe`, `dsr_p_value` 列を追加し、**1試行あたり** ではなく **スウィープ全体での選定** として読む。
- 推奨運用: `dsr_p_value < 0.05` を最低ライン、`< 0.01` を採用候補。

#### 5.2.3 サンプルサイズフィルタ

- `closed_trades < N_min` のケースはランキング対象外（spec で `min_trades: 30` を必須化）。
- `N_min` の根拠: 戦略の `expected_win_rate` と `target_r_multiple` から、Sharpe を有意検定できる最低サイズを `power_analysis(win_rate, r, alpha=0.05, power=0.8)` で算出。spec に書く前に CLI でレコメンド値を出す:

```bash
python -m research.scripts.recommend_min_trades \
  --win-rate 0.45 --r 1.8
# → recommended N_min = 42
```

#### 5.2.4 Out-of-sample 強制

これが **selection bias 対策の本丸**。スウィープ spec に `holdout` セクションを必須化:

```yaml
holdout:
  type: time_split
  train_end: 2025-11-30
  test_start: 2025-12-01    # 全 trial で共通の holdout
```

- パラメータ探索は train 区間のみで実行し、`runs/{run_id}/trials.parquet` の `train_summary` に保存。
- holdout 区間の結果は別カラム `holdout_summary` に保存し、`run_overview.ipynb` のランキングは **必ず holdout 数値を主軸に表示**する。
- train のメトリクスは「過学習診断用」の副次情報として扱う。

### 5.3 完了条件

- ランキング表の主軸が holdout メトリクス + CI + DSR p 値に切り替わっている。
- 過去の sweep で「train で1位だが holdout で大幅劣化」のケースが可視化される。
- `min_trades` 未満のケースは自動除外される。

---

## 6. Phase 7: Forward test 整合（shadow compare）

### 6.1 解決したい問題

「backtest で良かった」と「LIVE でうまくいく」のギャップは、refactor だけでは絶対に閉じない。**LIVE 実績と backtest を日次で diff する仕組み**だけがこのギャップを定量化できる。

### 6.2 改修内容

#### 6.2.1 Shadow backtest

- LIVE bot は実トレードを Firestore に記録している。
- 日次ジョブ:
  1. 過去 N 日の LIVE trade を Firestore から取得
  2. 同期間の OHLCV を `MarketDataset` でロード
  3. **LIVE が使った config をスナップショットして** backtest を実行
  4. LIVE trade と backtest trade を `(entry_time, direction)` でマッチング
  5. 不一致レポート `research/data/shadow_diff/{date}.json` に出力
  6. 閾値超過なら Slack alert

```bash
python -m research.scripts.shadow_compare \
  --broker GMO_COIN \
  --since 7d \
  --slack-on-threshold 0.05
```

#### 6.2.2 乖離の分解

`shadow_diff.json` には以下を出す:

- **同一 trade での fill 価格差** — 執行モデルの不正確さ
- **片側のみに存在する trade** — ロジック差 or データ差
- **PnL 累積差** — 上記の合算
- **乖離原因タグ**: `EXECUTION` / `DATA` / `LOGIC` / `GUARD_MISMATCH`

#### 6.2.3 LIVE 投入前ゲート

新パラメータを LIVE に出す前の手順:

1. backtest で採用候補化（Phase 6 のゲート通過）
2. **paper モードで N=30日 forward test**（`apps/*/config/*.json` の `mode: PAPER`）
3. 30日後に paper trade ログと backtest 予測を `shadow_compare` で diff
4. **trade 単位で 95% 一致 / 累積 PnL ±20%** を満たして初めて LIVE 最小サイズへ移行

これは README と PR template に明記。

### 6.3 完了条件

- 日次 shadow_compare が CI で回り、Slack 通知が機能する。
- 直近30日の LIVE と backtest の trade 単位一致率が常時可視化されている。
- PR template に「forward test 結果」セクションが追加されている。

---

## 7. Phase 8: データ代表性 / レジーム分解

### 7.1 解決したい問題

「直近1年で勝てた」だけでは、次の3ヶ月が bear に切り替わった瞬間に崩壊しうる。**レジーム別の成績**が見えていないと、知らずに「2024 bull 専用パラメータ」を採用する。

### 7.2 改修内容

#### 7.2.1 レジームタグ

`research/src/data/regime_tagger.py` 新規。バー列を入力に、各バーへ複数次元のレジームラベルを付与:

| 次元 | ラベル | 判定 |
|---|---|---|
| trend | BULL / BEAR / CHOPPY | 200-bar EMA 上下 + slope 閾値 |
| volatility | LOW_VOL / MID_VOL / HIGH_VOL | ATR pct の三分位 |
| btc_correlation | RISK_ON / RISK_OFF | BTC との相関ローリング |

タグ判定は `MarketDataset` 構築時に1回計算し、Parquet に列として保存。

#### 7.2.2 レジーム別メトリクス

`metrics.compute_summary` を拡張し、結果に以下を付ける:

```python
summary["by_regime"] = {
  "trend": {
    "BULL": {"trades": 32, "total_pnl_pct": 12.4, "win_rate_pct": 56.2, ...},
    "BEAR": {"trades": 18, "total_pnl_pct": -2.1, "win_rate_pct": 38.9, ...},
    "CHOPPY": {"trades": 8, "total_pnl_pct": 1.1, "win_rate_pct": 50.0, ...},
  },
  "volatility": {...},
}
```

#### 7.2.3 代表性ガード

採用ゲート（Phase 11）の構成要素:

- 全レジーム（trend × volatility の主要組合せ）で `total_pnl_pct > 0` であることを必須化。
- 1レジームでも `closed_trades < 5` ならフラグ（代表性不足）。

#### 7.2.4 Notebook 拡張

`run_overview.ipynb` に追加:

- レジーム別 PnL ヒートマップ（縦: ケース、横: regime）
- 「全レジームで正」のケースをハイライト表示

### 7.3 完了条件

- 過去スウィープ結果のうち、「直近1年は強いが BEAR 区間では負」のケースが notebook 上で1画面で識別できる。
- ゲートが機能して「BULL のみで稼いでいる」ケースが採用候補から自動除外される。

---

## 8. Phase 9: ルックアヘッド監査

### 8.1 解決したい問題

戦略コード（`apps/*/domain/strategy/...`）が知らずに未来情報を参照していると、backtest だけが綺麗な右肩上がりになる。コードレビューでは見落とすため、機械的検査が必須。

### 8.2 改修内容

#### 8.2.1 シャッフルテスト

`tests/research/test_lookahead.py` 新規:

```python
def test_shuffled_bars_yield_zero_expectation():
    bars = read_bars_from_csv("research/data/cache/.../sample.parquet")
    shuffled = bars.copy()
    random.shuffle(shuffled)
    # close_time は順序維持、open/high/low/close をシャッフル
    report = run_backtest(shuffled, config)
    assert abs(report.summary.total_scaled_pnl_pct) < 5.0, "look-ahead suspected"
```

戦略がランダム化されたバーで利益を出すなら未来参照の疑い。N=10 シードで反復し全シードで条件 PASS を要求。

#### 8.2.2 時間境界テスト

```python
def test_decision_is_stable_across_bar_window():
    for cut in range(100, len(bars)):
        decision_a = evaluate_strategy_for_model(bars=bars[:cut+1], ...)
        decision_b = evaluate_strategy_for_model(bars=bars[:cut+200], ...)
        # bars[cut] 時点の判定は周辺のバー数で変わってはいけない
        assert decision_a == decision_b
```

#### 8.2.3 上位足同期検査

15m + 4h 構成の戦略は、4h の判定が 15m の現バー時刻で「未来 4h バー」を見ていないか確認する単体テスト。

### 8.3 完了条件

- 上記3テストが CI 必須化。
- 既存戦略でいずれかが FAIL するなら Phase 9 の最優先事項として修正。

---

## 9. Phase 10: 運用ガード整合

### 9.1 確認済み

[research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py) は `loss_streak_trade_cap` / `short_regime_guard` / `short_stop_loss_cooldown` を既に組み込み済。**ここで重要なのは「新規ガードを追加した時に backtest 側にも入れ忘れない」運用**。

### 9.2 改修内容

- `apps/*/domain/risk/` 配下に新ファイルが追加された PR は、`research/src/domain/backtest_engine.py` のテストで参照されているか CI で確認する lint を追加。
- 「LIVE bot のみで有効なガード」「backtest のみで有効なガード」が無いことをチェックする整合テストを追加。

### 9.3 完了条件

- CI lint が機能し、ガード追加 PR が backtest 統合を強制する。

---

## 10. Phase 11: 採用ゲートの明文化

### 10.1 ゲート定義（README に明記する）

backtest → forward test → LIVE 投入の各段に**数値で測れる合否基準**を置く:

#### Gate A: backtest 採用候補

スウィープ結果のケースが採用候補になる条件（全 AND）:

1. `closed_trades >= recommend_min_trades(win_rate, r)`
2. `total_scaled_pnl_pct_ci_low > 0`（holdout 区間で）
3. `return_to_dd_ci_low > 0`
4. walk-forward 窓のうち **70% 以上が positive**
5. **全レジーム**（BULL / BEAR / CHOPPY）で `total_pnl_pct > 0`
6. `deflated_sharpe_p_value < 0.05`
7. `StochasticExecutionModel` の p05 シードでも `total_scaled_pnl_pct_ci_low > 0`

#### Gate B: forward test 通過

Gate A を通った候補は paper モードで最低30日:

8. 期間中の trade 単位一致率 `>= 95%`（backtest 予測との）
9. 累積 PnL の偏差が backtest 期待の `[-20%, +30%]` に収まる
10. 期間中の Slack alert（shadow_compare 由来）が0件

#### Gate C: LIVE 最小サイズ移行

Gate B を通ったら LIVE の `position_size_multiplier=0.5` で30日:

11. drawdown が backtest の p95 を超えない
12. 期間中の reject 率が `execution_profile` で記録された値の +50% 以内

Gate C 通過で本サイズ運用。

### 10.2 PR template への組込み

戦略・パラメータ変更 PR には次セクションを必須化:

```markdown
## Backtest validity report
- Run ID: <run_id>
- Gate A 合否: [PASS|FAIL] (項目別)
- DSR p value: ...
- All-regime positive: [Y|N]
- Linked notebook: research/notebooks/run_overview.ipynb (RUN_ID=...)

## Forward test status
- Paper period: yyyy-mm-dd 〜 yyyy-mm-dd
- Trade match rate: ...
- Shadow alerts: 0
```

### 10.3 完了条件

- README と PR template が更新済。
- `compare_runs.py --gate-a` で Gate A の合否を1コマンドで出せる。
- 過去の採用パラメータを遡及で Gate A 評価し、Gate を通らないケースが特定されている。

---

## 11. 実装順序（推奨）

依存関係から、姉妹文書 Phase 1–4 の完了後、本書は次の順で進める:

```
Phase 9 (ルックアヘッド監査)         ← 既存戦略の隠れバグを先に潰す。工数小。
   ↓
Phase 6 (統計的厳密性)              ← 既存 sweep に CI/DSR を載せるだけ。即効性大。
   ↓
Phase 8 (レジーム分解)              ← 6 と並行可。データ層拡張のみ。
   ↓
Phase 5 (執行モデル現実化)          ← 重い。LIVE ログ蓄積が必要。
   ↓
Phase 7 (Forward test 整合)         ← Phase 5 完了後にこそ意味を持つ。
   ↓
Phase 10 (運用ガード整合)           ← 並行可。CI lint だけ。
   ↓
Phase 11 (採用ゲート明文化)          ← 上記の積み上げを束ねる運用層。
```

**Phase 9 + 6 + 8 だけでも、現状から見ると「実践判断に足る」に7割程度近づく**。Phase 5 / 7 が完了して初めて「乖離が定量化された状態で LIVE 採用可」と言える。

---

## 12. 受け入れチェックリスト

本書の改修が完了した時に満たすべき項目:

- [ ] `IdealExecutionModel` と `StochasticExecutionModel` が選択可能で、両者の結果が `runs/` に並列保存される
- [ ] LIVE 実績から `execution_profile` を生成する CLI が稼働している
- [ ] 全 sweep 結果に bootstrap CI と Deflated Sharpe が付与される
- [ ] holdout を切らない sweep spec は CI で reject される
- [ ] レジームタグが `MarketDataset` に乗り、メトリクスが分解される
- [ ] シャッフルテスト・時間境界テストが CI 必須化されている
- [ ] 日次 `shadow_compare` が稼働し Slack alert が機能する
- [ ] PR template に backtest validity report セクションが存在する
- [ ] `compare_runs.py --gate-a` で Gate A 合否を出せる
- [ ] README に Gate A/B/C の数値基準が明記されている

これらが揃って初めて、「backtest が良かったから本番に出す」を組織的に再現可能にできる。
