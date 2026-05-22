# Phase 3-V findings — Mean reversion (Bollinger Band reversal)

- 計画書: [gmo_bot_post_kill_exploration_plan.md](gmo_bot_post_kill_exploration_plan.md) §2.4 (Phase 3)
- 前提: [Phase 1 findings](gmo_bot_post_kill_phase1_findings.md) で trend-following 系統全敗 → P3-V (chop 専用) を最低コスト軸として先行検証
- 実施日: 2026-05-21

## 0. 実装

新規ストラテジ [`apps/gmo_bot/domain/strategy/models/mean_reversion_15m_v0.py`](../apps/gmo_bot/domain/strategy/models/mean_reversion_15m_v0.py):

- **Entry**: BB(period, num_std) の外側で逆張り
  - LONG: close < lower_band AND ADX < adx_chop_max
  - SHORT: close > upper_band AND ADX < adx_chop_max
- **Stop**: 反対側の bar 端 ± `stop_atr_cushion * ATR`
- **TP**: R-multiple (`exit.take_profit_r_multiple`)
- **その他 filter**: `long_atr_pct_max` / `short_atr_pct_max` でボラ過熱時を除外

`registry.py` / `schema.py` / `_parse_strategy` に登録。テスト 314 / 314 pass.

## 1. 結果サマリー (Phase 3 Done 基準: pos_rate≥60, mean≥+3, min≥-5)

### 1.1 SOL/JPY 15m (windows=13, window_bars=3000)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| mean_reversion_default (BB20, std2.0, chop25) | -6.90 | -1.25 | 30.8 | REJECT |
| mean_reversion_bb30_chop20 | -9.21 | -4.15 | 7.7 | REJECT |
| mean_reversion_bb20_std2_5 | -8.70 | -2.28 | 23.1 | REJECT |

### 1.2 SOL/JPY 1h (windows=10, window_bars=1000)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| mean_reversion_default | -9.37 | -3.59 | 20.0 | REJECT |
| mean_reversion_bb30_chop20 | -5.27 | -3.08 | 10.0 | REJECT |
| mean_reversion_bb20_std2_5 | -6.60 | -3.89 | 10.0 | REJECT |

### 1.3 BTC/JPY 15m (windows=12, window_bars=3000)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| mean_reversion_default | -9.60 | -5.30 | 8.3 | REJECT |
| mean_reversion_bb30_chop20 | -9.96 | -4.00 | 8.3 | REJECT |
| mean_reversion_bb20_std2_5 | -11.89 | -4.57 | 0.0 | REJECT |

### 1.4 ETH/JPY 15m (windows=12, window_bars=3000)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| mean_reversion_default | -18.51 | -9.96 | 0.0 | REJECT |
| mean_reversion_bb30_chop20 | -11.46 | -4.55 | 33.3 | REJECT |
| mean_reversion_bb20_std2_5 | -13.37 | -4.79 | 16.7 | REJECT |

## 2. 観察と分析

### 2.1 systematic adverse selection

12 構成 (4 軸 × 3 variant) 全てで:
- mean PnL **全て負**
- pos_rate **0〜33%** 範囲（陽性 window が少数）
- min PnL **全て -5% より悪い**

これは「BB extremes で逆張りすると体系的に負ける」というシグナル。chop filter (ADX < 20-25) を加えても改善しないため、**adverse selection は chop 環境でも発生**している:

- BB 外側で逆張り → 多くの場合は **break out への initial move** に飛びつく形になり、stop hit に終わる
- chop と認識される ADX 低位環境でも、micro-trends が頻発し短期 break out が起きやすい

### 2.2 chop_max=20 で結果がさらに悪化する逆説

`bb30_chop20` (chop filter を厳しく) は SOL/JPY 15m で pos_rate 7.7% / mean -4.15 と default より大幅に悪化。
→ 「真の chop」では trade そのものが極めて少なくなり、sampling bias で負ける window が増える。chop 認識のための ADX threshold tuning では救えない。

### 2.3 ETH/JPY default の壊滅 (mean -9.96)

`adx_chop_max=25` だと ETH/JPY 15m で12 window 全て負け。これは ETH/JPY の chop が他通貨より volatile / wider-band を伴うためで、`bb20_std2.0` の entry threshold が浅すぎる。

## 3. 計画書 §2.4 撤退条件との照合

Phase 3 Done 基準 (Phase 2 同等 + max DD ≤ 5%):
- pos_rate ≥ 80% / min ≥ -2% / mean ≥ +5%

全 12 構成で **どの 1 つも基準の半分以下**。

撤退条件:
> P3-G/M/O のいずれも Phase 2 Done 基準を満たさない → bot 開発自体を一時停止する判断材料を提供

P3-V は plan §2.4 で「最低コスト」と明記された候補であり、ここで edge ゼロ確認 → **P3-G/M/O の実装コスト (80-160h) に対する期待値は更に低い**と推定される。

## 4. Phase 3 内の次手選択肢

### A. P3-G (Grid trading) を試す

- plan §2.4 で 実装コスト「大」、新規 engine 必須
- chop 想定だが trend で limit hit リスクあり
- 実装 50-80h、結果見えるまで 4-6 週

### B. P3-M (Market making) を試す

- 高頻度・低 win 幅、order placement model の精度依存
- シミュレーション精度を信用できない可能性高
- 実装 80-100h

### C. P3-O (Volatility harvesting)

- GMO で option が扱えるかから検証必要 → 実用性低い

### D. **bot 開発を一時凍結** (plan §6.1 撤退)

- P3-V の壊滅的結果 + Phase 1 結果と合わせ、SOL/JPY エコシステムで edge を取りに行く期待値は極めて低い
- plan §9 オプション B 相当: LIVE 停止 → 損失確定 / 開発リソース解放

### E. **問題定義の見直し** (plan 外オプション)

P3-V/G/M/O はいずれも「strategy 種別」を変えるアプローチ。一方:
- **データ前処理**: regime detection (HMM / change point) で entry filter を抜本的に変える
- **portfolio approach**: SOL/JPY + BTC/JPY + ETH/JPY を同時運用し pair-spread / basket arbitrage を狙う
- **microstructure**: tick / orderbook data に降りる (現状 OHLCV だけで判断している)

これらは plan の枠外で再構想が必要。

## 5. 推奨 (主観)

P3-V の **systematic negative pos_rate** は強い構造的シグナル。trend-following（Phase 1）+ mean reversion (P3-V) 両方で全敗 → SOL/JPY 系の 15m/1h OHLCV だけからは edge を抽出できない可能性が高い。

- **短期**: D (一時凍結) で出血を止め、開発リソースを別案件に振る
- **中期**: E (問題定義見直し) で別アプローチを構想
- P3-G 直行は実装コスト割に合わない可能性 (期待値低)

## 6. 出力ファイル

- `research/data/runs/phase3_v/soljpy_15m.json`
- `research/data/runs/phase3_v/soljpy_1h.json`
- `research/data/runs/phase3_v/btcjpy_15m.json`
- `research/data/runs/phase3_v/ethjpy_15m.json`

## 7. 既存テスト

- 314 / 314 pass (Phase 3-V 実装後も regression なし)
