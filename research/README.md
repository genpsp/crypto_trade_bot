# research

`research` は「分析専用」のディレクトリです。実運用 bot (`apps/*_bot`) と分離しつつ、戦略判定は `apps.*.domain.strategy.registry.evaluate_strategy_for_model` を呼び出して単一ソースを維持します。

## ディレクトリ

- `research/data/cache`: `(broker, pair, timeframe)` ごとの月次 Parquet OHLCV キャッシュ
- `research/data/raw`: 旧CSV入力（読み取り互換・移行元）
- `research/data/runs/{run_id}`: 新しいバックテスト結果ストア
  - `manifest.json`: spec snapshot / git sha / dataset hash / 実行時刻
  - `trials.parquet`: 1試行1行の結果
  - `trades/{trial_id}.parquet`: trade明細（`--keep-trades all` / `on-error` 指定時）
- `research/data/processed`: 旧JSON出力置き場（読み取り互換のみ。新規結果は `runs/` に保存）
- `research/sweeps`: 宣言的 sweep YAML
- `research/notebooks`: run store を読むNotebook
- `research/notebooks/_archive`: 旧 `backtest_playground_*.ipynb`
- `research/scripts`: CLIエントリ
- `research/src`: data / eval / sweep / store 層

## 1. データ同期

初回または日次更新では Parquet キャッシュを同期します。

```bash
python -m research.scripts.data_sync       --broker GMO_COIN --pair SOL/JPY --timeframe 15m       --since 2023-01-01
```

旧CSVをキャッシュへ移行する場合:

```bash
python -m research.scripts.migrate_csv_to_parquet       --input research/data/raw/soljpy_15m_1y.csv       --broker GMO_COIN --pair SOL/JPY --timeframe 15m
```

`fetch_ohlcv.py` は互換ラッパとして残しています。CSVが必要な場合のみ使ってください。

## 2. Sweep 実行

パラメータ・window・dataset は YAML で宣言します。

```bash
python -m research.scripts.run_sweep       --spec research/sweeps/gmo_15m_baseline_sensitivity.yaml       --workers 4

# trade明細も保存して trial_drilldown で資産曲線を確認する場合
python -m research.scripts.run_sweep       --spec research/sweeps/gmo_15m_baseline_sensitivity.yaml       --workers 4       --keep-trades all
```

出力例:

```text
research/data/runs/20260515-094012-gmo-15m-baseline-sensitivity-a1b2c3d/
├── manifest.json
├── trials.parquet
└── trades/{trial_id}.parquet  # --keep-trades 指定時
```

smoke test では `--max-trials 1` を使えます。

## 3. 結果比較

ランキング:

```bash
python -m research.scripts.compare_runs --run latest --metric return_to_dd --top 10
```

軸別マージナル:

```bash
python -m research.scripts.compare_runs --run latest --metric return_to_dd --marginal
```

run間diff:

```bash
python -m research.scripts.compare_runs --runs RUN_ID_A,RUN_ID_B --metric return_to_dd
```

## 4. Notebook

`jupyter lab` を起動して次のNotebookを開き、冒頭の Parameters セルだけ編集して `Run All` します。Notebook内ではバックテストを再実行せず、`research/data/runs/` の Parquet を読むだけです。

- `research/notebooks/run_overview.ipynb`: 1 run のランキング・軸別マージナル・window安定性
- `research/notebooks/run_diff.ipynb`: 2 run の改善/劣化比較
- `research/notebooks/trial_drilldown.ipynb`: 1 trial の config / summary / no-signal / trade明細（保存時）

## 5. 開発ルール

- 戦略パラメータは `config["strategy"]` 経由で上書きします。`setattr(module, ...)` の monkey-patch は禁止です。
- 新規の実験結果は `research/data/runs/` に保存します。`research/data/processed/*_latest.json` は旧資産の読み取り互換扱いです。
- ロジック改修PRでは `compare_runs.py` または `run_diff.ipynb` の回帰確認結果を添付してください。
