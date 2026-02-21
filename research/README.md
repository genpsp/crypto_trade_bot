# research

`research` は「分析専用」のディレクトリです。  
実運用 bot (`pybot`) と分離しつつ、戦略ロジックは `pybot` を直接呼んで共有します。

## 役割

- `pybot`: 実売買実行（LIVE/PAPER、Firestore、Redis、Jupiter）
- `research`: データ取得・バックテスト・検証レポート

この分離は正しいです。理由は、分析試行錯誤の変更を実運用コードに混ぜないためです。

## 共有ロジック

バックテストのエントリー判定は次を直接使用します。

- `pybot.domain.strategy.ema_trend_pullback_v0.evaluate_ema_trend_pullback_v0`

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
  --limit 1000 \
  --output research/data/raw/solusdc_2h.csv
```

```bash
python -m research.scripts.run_backtest \
  --config research/config.example.json \
  --bars research/data/raw/solusdc_2h.csv \
  --output research/data/processed/backtest_latest.json
```

`--config` は `config/current` と同スキーマ JSON を使ってください。初期値は `research/config.example.json` をベースに調整できます。
