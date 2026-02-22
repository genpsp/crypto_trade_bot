# research

`research` は「分析専用」のディレクトリです。  
実運用 bot (`pybot`) と分離しつつ、戦略ロジックは `pybot` を直接呼んで共有します。

## 役割

- `pybot`: 実売買実行（LIVE/PAPER、Firestore、Redis、Jupiter）
- `research`: データ取得・バックテスト・検証レポート

この分離は正しいです。理由は、分析試行錯誤の変更を実運用コードに混ぜないためです。

## 共有ロジック

バックテストのエントリー判定は次を直接使用します。

- `pybot.domain.strategy.registry.evaluate_strategy_for_model`

つまり、戦略条件は単一ソースで管理されます。

## ディレクトリ

- `research/data/raw`: 収集したOHLCV CSV
- `research/data/processed`: バックテスト結果 JSON
- `research/notebooks`: Notebook置き場
- `research/scripts`: CLIエントリ
- `research/src`: 分析用モジュール（adapters/app/domain/infra）

## 使い方

リポジトリルートで実行:

```bash
python -m research.scripts.fetch_ohlcv \
  --pair SOL/USDC \
  --timeframe 2h \
  --years 2 \
  --output research/data/raw/solusdc_2h.csv
```

上記は初回に2年分をCSV保存し、2回目以降は既存CSVを再利用します。  
強制再取得したい場合のみ `--refresh` を付けてください。

```bash
python -m research.scripts.run_backtest \
  --config research/models/core_long_v0/config/current.json \
  --bars research/data/raw/solusdc_2h.csv \
  --output research/data/processed/backtest_latest.json
```

`--config` は `research/models/<model_id>/config/current.json` を指定します。  
モデル設定はこの1ファイルに集約されています。

## Notebook で実行（コピペ不要）

`jupyter lab` を起動したら下記ノートを開いて、`Run -> Run All Cells` を実行してください。

- ロングモデル: `research/notebooks/backtest_playground.ipynb`
- ショートモデル: `research/notebooks/backtest_playground_short.ipynb`

- パラメータはノート内の `Parameters` セルだけ編集
- `REFRESH_DATA=False` なら既存CSVを再利用
- 結果は `research/data/processed/backtest_latest.json` に保存
