# research ディレクトリ バックテスト基盤 再設計指示書

## 1. 背景と現状の問題

現在 `research/` 配下では戦略ロジックのオフライン検証を行っているが、`fetch_ohlcv` / `run_backtest` / `run_walk_forward` / `analyze_gmo_15m_param_sweep` などのスクリプトが独立に積み上がっており、ロジック比較を「効率的かつシステマティックに」回すための共通基盤が無い。

### 1.1 現状観測される具体的な問題

- **データ取得とキャッシュ**:
  - [research/scripts/fetch_ohlcv.py](../research/scripts/fetch_ohlcv.py) は出力パス単位でしかキャッシュせず、「`solusdc_15m_1y.csv`」「`solusdc_15m_3y.csv`」のように **期間ごとに別ファイル** が並ぶ。`(pair, timeframe)` で再利用できる単一ソースが無い。
  - 「既存CSVのバー数が要求以上ならスキップ」というロジックのため、**増分取得（昨日分だけ追記）** ができない。長期検証で毎回フルフェッチを誘発する。
  - メタデータ（取得日時、ソース、欠損区間、ティッカー仕様）が CSV に同梱されておらず、再現性監査が困難。
- **バックテスト実行**:
  - `run_backtest.py` は単発の `(config, bars)` 実行に閉じていて、**期間スウィープ**や**複数モデル比較**の概念が無い。
  - `run_walk_forward.py` は窓ロールはできるが、`(train_days, test_days, step_days)` を CLI で1セット渡すだけ。複数パラメータの直交スウィープができない。
- **パラメータスウィープ**:
  - [research/scripts/analyze_gmo_15m_param_sweep.py](../research/scripts/analyze_gmo_15m_param_sweep.py) は `SweepCase` を **手書き列挙** し、モジュール属性を `setattr` / 復元する**モンキーパッチ方式**。
  - 軸定義（直交格子・ペアワイズ・任意のグリッド）も、ロジックモデルごとの汎用化も無い。
  - GMO 15m 専用に固定。`ema_pullback_2h_long_v0` や `storm_2h_short_v0` には流用不能。
- **結果ストア**:
  - [research/data/processed/](../research/data/processed/) にフラットな JSON が30ファイル以上、`*_latest.json` で**上書き運用**。実験の系譜（lineage）が消える。
  - 比較は notebook で都度ロード。**ランキング・差分・回帰確認**ができるクエリ層が無い。
- **ノートブック重複**:
  - `backtest_playground.ipynb` / `backtest_playground_short.ipynb` / `backtest_playground_15m_1y.ipynb` / `backtest_playground_gmo_15m_1y.ipynb` がほぼ同じ構造の派生品で、500KB級が4本。改修コストが指数増。

### 1.2 ゴール

> **「複数の戦略モデル × 複数のパラメータセット × 複数の期間窓 × 複数の市場」の総当たり評価を、単一CLI／ノートで宣言的に回し、永続的・比較可能な形で結果が蓄積される基盤」**

を構築する。

---

## 2. 設計原則

| 原則 | 内容 |
|---|---|
| **戦略ロジックの単一ソース** | エントリー判定は引き続き `apps.*.domain.strategy.registry.evaluate_strategy_for_model` を再利用する。research はあくまで「実行・収集・比較」のレイヤ。 |
| **データはパーティション化・増分更新可能に** | `(broker, pair, timeframe)` を主キーに、月次パーティションで保存。**フェッチは差分のみ**。 |
| **設定はコードでなくデータ** | スウィープ定義（軸×値・期間窓・市場）は YAML で記述。スクリプトはランナーであり、ロジック列挙を持たない。 |
| **モンキーパッチを廃止** | 戦略パラメータは `config["strategy"]` 経由でのみ上書きする。`setattr(module, ...)` は禁止。 |
| **結果は構造化ストアに append** | フラット JSON 単発ではなく、行指向（Parquet / SQLite）に1試行=1レコードで蓄積。`run_id` で系譜を辿れる。 |
| **並列実行可能** | 1試行は純粋関数 `(config, bars_slice) -> summary`。`concurrent.futures.ProcessPoolExecutor` で素直にスケールできる構造にする。 |
| **既存資産の段階移行** | 既存スクリプトは消さず、新基盤に薄いアダプタを噛ませて並走させる。Phase 完了時に旧スクリプトを削除。 |

---

## 3. ターゲット構成

```
research/
├── src/
│   ├── data/                       # NEW: データ層
│   │   ├── market_dataset.py       #   MarketDataset (pair, timeframe, range, bars)
│   │   ├── partitioned_cache.py    #   月次パーティション、増分フェッチ
│   │   ├── source_registry.py      #   broker→provider のディスパッチ（既存 _build_provider を移管）
│   │   └── slicer.py               #   日付/インデックス範囲スライサ
│   ├── eval/                       # NEW: 評価層
│   │   ├── trial.py                #   1試行の入出力 (TrialSpec, TrialResult)
│   │   ├── runner.py               #   trial→backtest 実行・並列化
│   │   ├── metrics.py              #   total_pnl, win_rate, return_to_dd, sharpe, profit_factor, …
│   │   └── window.py               #   walk-forward / rolling / fixed windows ジェネレータ
│   ├── sweep/                      # NEW: スウィープ層
│   │   ├── grid.py                 #   軸定義 → デカルト積 / ペアワイズ
│   │   ├── spec_loader.py          #   YAML → SweepSpec
│   │   ├── overrides.py            #   config dict への deep-merge オーバライド適用
│   │   └── plan.py                 #   (cases × windows × datasets) を Trial 列に展開
│   ├── store/                      # NEW: 結果ストア
│   │   ├── trial_store.py          #   Parquet (or SQLite) への append / クエリ
│   │   ├── lineage.py              #   run_id / config_hash / data_hash
│   │   └── views.py                #   ランキング・差分テーブル生成
│   ├── domain/                     # 既存（backtest_engine, backtest_types）
│   ├── adapters/                   # 既存（csv_bar_repository は data/ 経由に置換予定）
│   ├── app/                        # 既存（backtest_usecase は eval/ に統合）
│   └── infra/                      # 既存（research_config）
├── scripts/
│   ├── data_sync.py                # NEW: 増分フェッチ CLI
│   ├── run_sweep.py                # NEW: YAML 指定で総合実行
│   ├── compare_runs.py             # NEW: run_id 群の差分・ランキング表示
│   ├── fetch_ohlcv.py              # 既存 → data_sync の薄いラッパに退避
│   ├── run_backtest.py             # 既存 → run_sweep の単一試行ショートカットに退避
│   ├── run_walk_forward.py         # 既存 → run_sweep に統合後、削除
│   └── analyze_gmo_15m_param_sweep.py  # 既存 → YAML 化して削除
├── sweeps/                         # NEW: スウィープ定義 YAML
│   ├── gmo_15m_baseline_sensitivity.yaml
│   ├── dex_15m_walk_forward.yaml
│   └── ...
├── data/
│   ├── raw/                        # 既存 CSV は段階移行（読み取り互換は維持）
│   ├── cache/                      # NEW: 月次パーティション Parquet
│   │   └── gmo/soljpy/15m/2024-01.parquet  ...
│   └── runs/                       # NEW: run_id 単位ディレクトリ
│       └── {run_id}/
│           ├── manifest.json       # spec, env, git_sha, data_hash
│           ├── trials.parquet      # 1試行1行
│           └── trades/             # 任意：トレード明細（重い）
└── models/                         # 既存維持
```

---

## 4. コンポーネント詳細

### 4.1 データ層（`research/src/data/`）

#### 4.1.1 パーティションキャッシュ

- 保存形式: **Parquet 月次パーティション**（圧縮率と読込速度のため。CSV からの移行手順は §6 で記述）。
- パス規約: `research/data/cache/{broker}/{pair_safe}/{timeframe}/{YYYY-MM}.parquet`
  - `pair_safe`: `SOL/JPY` → `soljpy` のようにスラッシュを除去。
- 1ファイルのスキーマ: `open_time, close_time, open, high, low, close, volume, source, fetched_at`
- **増分フェッチ規約**:
  1. 既存パーティション群を走査し、最新の `close_time` を求める。
  2. プロバイダから `(latest_close_time, now]` 区間のみリクエスト。
  3. 取得結果を該当月パーティションに append（重複は `close_time` で de-dup）。
  4. メタファイル `research/data/cache/{broker}/{pair_safe}/{timeframe}/_manifest.json` に `last_synced_at`, `bar_count`, `gaps[]` を更新。
- **欠損検出**: 期待 bar 間隔（timeframe）と実差分を比較し、欠損区間を `gaps[]` に記録。サマリ印字でユーザに気付かせる。

#### 4.1.2 MarketDataset

```python
@dataclass(frozen=True)
class MarketDataset:
    broker: Literal["DEX", "GMO_COIN"]
    pair: str
    timeframe: str
    start: datetime
    end: datetime
    bars: list[OhlcvBar]
    data_hash: str  # sha256(bars[0].close_time + bars[-1].close_time + len(bars))

    def slice(self, start: datetime, end: datetime) -> "MarketDataset": ...
    def slice_by_bar_count(self, end_index: int, count: int) -> "MarketDataset": ...
```

- バックテスト関数は `MarketDataset.bars` を受け取り、`data_hash` は結果ストアに記録される（再現性検証用）。

#### 4.1.3 ソースレジストリ

- 既存の `fetch_ohlcv.py::_build_provider` を `source_registry.py::get_provider(broker, pair)` に移管。
- 追加銘柄・追加 broker は登録1行で済む形にする。

---

### 4.2 評価層（`research/src/eval/`）

#### 4.2.1 Trial 抽象

```python
@dataclass(frozen=True)
class TrialSpec:
    trial_id: str                 # deterministic hash of (config, dataset_key, window)
    model_id: str                 # e.g. "gmo_ema_pullback_15m_both_v0"
    config: BotConfig             # 完全な BotConfig（オーバライド適用済）
    dataset_key: DatasetKey       # broker, pair, timeframe
    window: WindowSpec            # 期間（絶対 / 相対 / walk-forward 窓ID）
    tags: dict[str, str]          # sweep_case_name, axis_values, etc.

@dataclass
class TrialResult:
    trial_id: str
    summary: dict[str, Any]       # metrics
    no_signal_reason_counts: dict[str, int]
    runtime_seconds: float
    error: str | None             # 例外時のみ
```

- **TrialSpec.trial_id は config と dataset_key と window から決定的に生成**（`hashlib.sha256(json.dumps(sort_keys=True))[:16]`）。同じ試行を再度キックしてもキャッシュヒットで省略可能。

#### 4.2.2 Runner

- `runner.run_trials(trials: Iterable[TrialSpec]) -> Iterator[TrialResult]`
- デフォルトは逐次。`--workers N` で `ProcessPoolExecutor` 並列。バックテスト関数が純粋（既存 `run_backtest`）であることを前提とするため、ロジック側に状態を持たせない約束を **新規にコードレビュー観点として明文化** する。
- 1試行あたりの計測: 経過秒、ピークメモリ（任意）、トレード数、no-signal カウント。
- 例外時は `error` フィールドに記録し他の試行は継続。

#### 4.2.3 Window ジェネレータ

```python
WindowSpec = Union[
    FixedWindow(start: datetime, end: datetime),
    RollingWindow(length_days: float, step_days: float),
    WalkForwardWindow(train_days: float, test_days: float, step_days: float),
    LastNDays(days: float),
]
```

- 既存 `run_walk_forward.py` のセグメント分割ロジック（連続バー判定）はそのまま `eval/window.py` に移管。

#### 4.2.4 Metrics

- 統一サマリ関数 `metrics.compute_summary(trades) -> dict` を1本化する。現在 `BacktestSummary` と `analyze_gmo_15m_param_sweep._summarize_report` が**別実装**になっているのを統合。
- 含めるべき指標:
  - `closed_trades`, `wins`, `losses`, `win_rate_pct`
  - `total_scaled_pnl_pct`, `second_half_scaled_pnl_pct`
  - `max_drawdown_pct_points`, `return_to_dd`
  - `average_r_multiple`, `profit_factor`
  - `expectancy_pct`, `sharpe_proxy`（日次集計）
  - `position_size_multiplier_counts`
  - `gross_long_pnl_pct`, `gross_short_pnl_pct`（BOTH モデル比較用）

---

### 4.3 スウィープ層（`research/src/sweep/`）

#### 4.3.1 YAML スウィープ仕様

例: `research/sweeps/gmo_15m_baseline_sensitivity.yaml`

```yaml
name: gmo_15m_baseline_sensitivity
model_id: gmo_ema_pullback_15m_both_v0
base_config: research/models/gmo_ema_pullback_15m_both_v0/config/current.json

dataset:
  broker: GMO_COIN
  pair: SOL/JPY
  timeframe: 15m

windows:
  - type: last_n_days
    days: 365
  - type: walk_forward
    train_days: 180
    test_days: 90
    step_days: 90

axes:
  - path: risk.max_trades_per_day
    values: [2, 3, 4]
  - path: risk.volatile_size_multiplier
    values: [0.4, 0.55, 0.7]
  - path: exit.take_profit_r_multiple
    values: [1.6, 1.8, 2.0]

combinations: full_grid     # full_grid | pairwise | listed
# listed の場合は cases: [{...}, {...}] を列挙
```

- `axes[].path` は config への dotted-path。`overrides.apply_path(config, path, value)` で deep-merge する。
- **戦略ロジック側のグローバル定数（例: `LONG_WEAK_UPPER_TREND_MIN_GAP_PCT`）は config 化を前提とする**。当面は強制的に config に新フィールドを追加し、戦略コード側で `config["strategy"].get(...)` で読む形に整える。これにより monkey-patch は廃止できる（§6 Phase 2 で対応）。

#### 4.3.2 Plan 生成

```python
def build_plan(spec: SweepSpec) -> list[TrialSpec]:
    base = load_bot_config(spec.base_config)
    cases = expand_cases(spec.axes, spec.combinations)  # list[dict[path,value]]
    windows = expand_windows(spec.windows, dataset)      # list[WindowSpec]
    return [
        make_trial(model_id, apply_overrides(base, c), dataset_key, w, tags={...})
        for c in cases for w in windows
    ]
```

---

### 4.4 結果ストア（`research/src/store/`）

#### 4.4.1 1 run = 1 ディレクトリ

```
research/data/runs/{run_id}/
├── manifest.json    # sweep spec snapshot, git_sha, started_at, finished_at,
│                    # python_version, data_hashes
├── trials.parquet   # 1試行=1行: trial_id, tags(json), summary(json展開済の主要列), error
└── trades/{trial_id}.parquet  # 必要時のみ（--keep-trades）
```

- `run_id`: `YYYYMMDD-HHMMSS-{spec_name}-{git_sha[:7]}`
- **manifest.json の data_hashes** は使用した MarketDataset.data_hash を全部記録。後日「同じデータで再実行したら同じ結果になるか」検証可能。

#### 4.4.2 クエリ

- `trial_store.load(run_id) -> pl.DataFrame`（polars もしくは pandas）
- `views.rank(df, by="return_to_dd", desc=True, top_k=10)`
- `views.diff(df_a, df_b, key=["axis_values"])` — 同一軸条件で run 間差分。
- `scripts/compare_runs.py --runs a,b,c --metric return_to_dd` で標準出力に要約表。

---

## 5. CLI / ユーザ体験

### 5.1 データ同期

```bash
python -m research.scripts.data_sync \
  --broker GMO_COIN --pair SOL/JPY --timeframe 15m \
  --since 2023-01-01
# → 既存パーティション最新時刻〜現在 を増分フェッチ。--since 以前が無い場合は遡及フル取得。
```

### 5.2 スウィープ実行

```bash
python -m research.scripts.run_sweep \
  --spec research/sweeps/gmo_15m_baseline_sensitivity.yaml \
  --workers 4 \
  --keep-trades none|on-error|all
# → run_id を出力し、research/data/runs/{run_id}/ に書き込む。
```

### 5.3 結果比較

```bash
python -m research.scripts.compare_runs \
  --run latest \
  --metric return_to_dd \
  --top 10
python -m research.scripts.compare_runs \
  --runs 20260510-...,20260512-... \
  --diff axis=exit.take_profit_r_multiple
```

### 5.4 Notebook

判断のしやすさは **「数字を眺める」ではなく「同じ画面で比較・順位・分解ができる」** ことで決まる。現状の `backtest_playground_*.ipynb` は1モデル1期間の単発実行で、比較は人間が複数ノートを切り替えるしかない。新基盤では以下の3本構成に整理する。

#### 5.4.1 `research/notebooks/run_overview.ipynb`（**判断の入り口**）

入力は `RUN_ID` 1個。`run_id="latest"` で直近を自動ロード。以下を上から順に固定セルで表示する。

1. **Run マニフェスト** — spec 名、git_sha、データ範囲、試行数、実行時間、エラー件数。
   - 「使ったコードと使ったデータが何だったか」を1画面で把握する。
2. **ランキング表（top 20）** — `return_to_dd` を主軸、副軸として `total_scaled_pnl_pct` / `closed_trades` / `max_drawdown_pct_points` / `win_rate_pct` / `second_half_scaled_pnl_pct` / `gross_long_pnl_pct` / `gross_short_pnl_pct` を列挙。
   - 「勝ち筋」を即座に絞る。第2半期 PnL を必ず横に置くのは過学習を見抜くため。
3. **軸別マージナルテーブル** — 各 axis の値ごとに「平均/中央値の return_to_dd」「ケース数」。
   - 例: `take_profit_r_multiple=1.6` の全ケース平均 vs `=1.8` の全ケース平均。
   - 「単独で効いている軸」と「組み合わせでしか効かない軸」が分かる。
4. **2軸ヒートマップ群** — 任意の axis ペアを選んで `return_to_dd` を色で表示。axis が3つ以上なら主要ペアの組合せを自動列挙。
   - 鞍点や谷を視覚的に検出する。
5. **期間 window 別の安定性プロット** — 同じパラメータケースで window をまたいだ箱ひげ図 / 折れ線。
   - 横軸=window 開始日、縦軸=`total_scaled_pnl_pct`。「直近1年は強いが walk-forward では崩れる」ケースを見抜く。
6. **エラー一覧** — `error` が非 NULL の trial の trial_id, tags, 例外メッセージ。

#### 5.4.2 `research/notebooks/run_diff.ipynb`（**run 間比較**）

入力は `RUN_ID_A`, `RUN_ID_B`（例: ロジック改修前と後、データ更新前と後）。

1. **軸条件で結合した比較表** — 同じ `tags.axis_values` を持つ行を outer join し、`Δ return_to_dd` / `Δ total_pnl` / `Δ closed_trades` を表示。
2. **改善・劣化ヒートマップ** — Δ を緑〜赤で塗る。
3. **片側にしか存在しないケース** — 片方の run で削除/追加された軸値を列挙（spec が変わった時に必要）。
4. **回帰検知サマリ** — 「上位10ケースのうち何件が劣化したか」を1行で出す。**ロジック改修の最終承認材料**。

#### 5.4.3 `research/notebooks/trial_drilldown.ipynb`（**1ケース深掘り**）

ランキングで気になった `trial_id` を入れる。

1. **その trial の config / window 表示**。
2. **資産曲線・ドローダウン曲線** — `trades/{trial_id}.parquet` から復元（`--keep-trades all` で実行された run のみ）。
3. **トレード明細テーブル** — 入出、direction、size_multiplier、exit_reason、scaled_pnl、保有時間。フィルタ用 dropdown 付き。
4. **no_signal_reason 内訳** — bar カウント上位10件と、対応する config キーへのリンク（マークダウン）。
5. **方向別分解** — LONG/SHORT の件数・勝率・平均 R。BOTH モデルの偏りを検査する。
6. **ガード発動タイムライン** — `SHORT_REGIME_GUARD` `SHORT_STOP_LOSS_COOLDOWN` `LOSS_STREAK_TRADE_CAP` の発動を時系列に並べる。「ガードが過保護で entry を殺している」のを発見する。

#### 5.4.4 設計上の約束

- **重い計算は notebook に入れない**。すべて `run_sweep` 実行時に書き出された Parquet からの読込のみ。再実行が秒で終わる。
- **入力は冒頭セル1個に集約**。`RUN_ID="latest"` と `TRIAL_ID=None` だけ。CLI と同じパラメータ感覚にする。
- **共通描画ユーティリティは `research/src/store/views.py` に置く**。notebook は呼び出すだけ。表/図のスタイルを統一する。
- **`*_latest.json` を読むレガシーノート4本は archive ディレクトリに退避**し、Phase 4 完了で削除候補とする。

---

## 6. 移行ロードマップ

実装は4フェーズに分割し、各 Phase の終了条件を明示する。**1 Phase = 1 PR** を原則とする。

### Phase 1: データ層リプレース（最優先・独立）

- [ ] `research/src/data/partitioned_cache.py` 実装（Parquet 読書 + 月次分割 + 重複排除）
- [ ] `research/src/data/source_registry.py` に既存プロバイダディスパッチを移管
- [ ] `research/scripts/data_sync.py` 新規。`fetch_ohlcv.py` は内部で `data_sync` を呼ぶ薄いラッパに置換し、既存出力 CSV の互換生成を維持する（フラグ）。
- [ ] 既存 `research/data/raw/*.csv` を1回限りのマイグレーションスクリプトで Parquet パーティションに変換する。`scripts/migrate_csv_to_parquet.py` 一発実行。
- **完了条件**: 既存ノートが `MarketDataset.load(broker, pair, timeframe, last_n_days=365)` でパーティションキャッシュから読めること。バックテスト結果が CSV 経由と一致すること（ハッシュ比較テスト）。

### Phase 2: 戦略パラメータの config 化

- [ ] `apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py` 等のモジュールグローバル定数（例: `LONG_WEAK_UPPER_TREND_MIN_GAP_PCT`, `MAX_DISTANCE_FROM_EMA_FAST_PCT`, `RSI_LONG_UPPER_BOUND`, `SHORT_UPPER_TREND_MIN_GAP_PCT`）を **`config["strategy"]` のフィールド** に昇格。
- [ ] `research/models/*/config/current.json` および本番 `apps/*/config/*.json` を更新し、現行値を明示する。
- [ ] `analyze_gmo_15m_param_sweep.py` の `setattr(gmo_strategy, ...)` 呼び出しを削除可能にする。
- **完了条件**: 戦略コードに `from ... import LONG_WEAK_UPPER_TREND_MIN_GAP_PCT` を捨て、全アクセスが `strategy_config.get(...)` 経由になる。本番動作との等価性テスト（同じ config を流して同じ trade 列）が PASS。

### Phase 3: 評価＋スウィープ層構築

- [ ] `eval/trial.py`, `eval/runner.py`, `eval/window.py`, `eval/metrics.py` 実装。
- [ ] `sweep/grid.py`, `sweep/spec_loader.py`, `sweep/overrides.py`, `sweep/plan.py` 実装。
- [ ] `scripts/run_sweep.py` 実装＋既存 walk-forward の YAML 表現1本を `sweeps/` に追加。
- [ ] `run_walk_forward.py` を `run_sweep.py` 呼び出しに退避（後で削除）。
- **完了条件**: 既存 `analyze_gmo_15m_param_sweep.py` と等価なスウィープを YAML で表現し、ranked リストが既存結果と一致。

### Phase 4: 結果ストア＋比較ツール

- [ ] `store/trial_store.py`, `store/lineage.py`, `store/views.py` 実装。`views` には notebook 共通描画ユーティリティ（ランキング表・ヒートマップ・資産曲線・diff 表）を集約する。
- [ ] `scripts/compare_runs.py` 実装。
- [ ] notebook を **`run_overview` / `run_diff` / `trial_drilldown` の3本構成**に再編し、`research/data/runs/` を起点に描画する形に書き直す（詳細は §5.4）。
- [ ] 旧 `backtest_playground_*.ipynb` 4本を `research/notebooks/_archive/` に退避。
- [ ] `research/data/processed/` の `*_latest.json` 上書き運用を廃止し、新規結果は `runs/` に書く。旧 JSON は読み取りのみ。
- **完了条件**:
  - `compare_runs.py` で過去30日分のスウィープ結果から「上位パラメータ」「期間別 P&L 推移」を1コマンドで出せる。
  - `run_overview.ipynb` を「`RUN_ID="latest"` で Run All」した時点で、ランキング・軸別マージナル・ヒートマップ・期間安定性プロットが表示され、**1試行を選んで `trial_drilldown.ipynb` に渡せば資産曲線まで30秒以内に出る**。
  - ロジック改修 PR では `run_diff.ipynb` の回帰検知サマリを必ず添付する運用を README に明記。

---

## 7. 設計上の決定と代替案

### 7.1 Parquet vs SQLite

採用は **Parquet (パーティション)**。理由:
- バー系列は読み出しが range-scan 主体で、列指向＆圧縮の恩恵が大きい。
- 並列読込が容易。pandas / polars / DuckDB から直接クエリできる。
- 既存 `OhlcvBar` データクラスと相性が良い（dataclass → arrow 変換ヘルパで足りる）。

代替: SQLite。`gaps` 検出や transactional な append は楽だが、列圧縮が効かず長期 15m データ（数百万行）でサイズが膨れる。**結果ストア側（trials.parquet）は Parquet で固定**、データ層も Parquet で揃える。

### 7.2 並列実行のプロセスモデル

`ProcessPoolExecutor` 採用。`backtest_engine.run_backtest` は副作用が無いため、`(config, bars)` を pickle して投げるだけで動く。CPU 集約なので GIL 回避のために thread ではなく process。MarketDataset は親側で1度ロードし、子に share する（fork セマンティクスで OK、Windows なら spawn でも数百万バー程度なら許容）。

### 7.3 戦略パラメータ昇格を「config 化」で行うか「外部オーバーライド」で行うか

**config 化**を選ぶ。理由:
- monkey-patch は本番コードと research でズレを生む（テストでは設定値、本番では定数）。
- すでに `BotConfig.strategy` という辞書フィールドがあるので拡張は素直。
- 将来 LIVE bot 側でもパラメータ調整したくなったとき、コード変更不要になる。

### 7.4 スウィープ YAML の表現力

`full_grid` / `pairwise` / `listed` の3形式に絞る。Optuna 等の最適化ライブラリ統合は将来検討（`spec.type: optuna` を追加するだけで Plan を Optuna study に差し替えられる構造にしておく）。本フェーズではグリッドサーチのみ。

---

## 8. 既存資産との互換性

- [research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py)・[research/src/domain/backtest_types.py](../research/src/domain/backtest_types.py) は変更しない。新規層から呼び出す。
- [research/src/infra/research_config.py](../research/src/infra/research_config.py) は維持。`sweep/overrides.py` が `load_bot_config` 後の dict を deep-merge する。
- 既存 CSV を消すのは Phase 1 マイグレーション完了後、PR 単位で削除する。

---

## 9. 影響範囲チェックリスト（AGENTS.md ルール準拠）

Phase 2 で戦略コードに触るため、変更前に以下を点検する。

- [ ] `apps/gmo_bot` LIVE bot の config schema (`apps/gmo_bot/infra/config/schema.py`) に新フィールドを追加する。
- [ ] 本番 `secrets/` 配下の運用 config（VPS 上のもの）に既存値を明示書き込み。
- [ ] `tests/` で本番 config を読む等価性テストを追加。
- [ ] DEX bot 側の戦略コードも同様にグローバル定数があれば洗い出し（Phase 2 の前作業）。

---

## 10. 出来上がりイメージ

最終的にロジック検証は以下の流れになる:

```bash
# 1. データを最新化（差分のみ。初回は遡及）
python -m research.scripts.data_sync --broker GMO_COIN --pair SOL/JPY --timeframe 15m --since 2023-01-01

# 2. スウィープ実行
python -m research.scripts.run_sweep --spec research/sweeps/gmo_15m_baseline_sensitivity.yaml --workers 4
# → run_id=20260515-094012-gmo_15m_baseline_sensitivity-a1b2c3d

# 3. ランキング確認
python -m research.scripts.compare_runs --run 20260515-094012-... --metric return_to_dd --top 10

# 4. notebook で深掘り
jupyter lab research/notebooks/run_analysis.ipynb   # run_id を変数で渡すだけ
```

「期間 × パラメータ × モデル × 市場」の総合評価が、ロジック追加時に **YAML 1枚** で表現できる状態がゴール。
