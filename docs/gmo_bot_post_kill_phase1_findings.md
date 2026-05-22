# Phase 1 axis-sweep findings (SOL/JPY post-kill exploration)

- 計画書: [gmo_bot_post_kill_exploration_plan.md](gmo_bot_post_kill_exploration_plan.md) §2.2 (Phase 1)
- 実施日: 2026-05-21
- スコープ: H1〜H3 仮説の低コスト反証 — timeframe / pair / trend detection 軸で v0 / v2-best / Supertrend / Donchian を rolling 評価

## 0. 実装サマリー

Phase 1 用に以下を実装:

1. **`research/scripts/resample_ohlcv.py`** — 15m OHLCV を 1h / 4h / 1d に集約 (incomplete 最終バケットのみ drop、メンテ gap を考慮し中間バケットは部分でも保持)
2. **`research/scripts/explore_phase1_axis_sweep.py`** — pair / timeframe / variant を引数化した rolling 評価
3. **`research/scripts/fetch_gmo_pair_15m_paced.py`** — rate-limit 対策付き 1.x 年分の OHLCV backfill
4. **スキーマ拡張** (production code):
   - `SignalTimeframe` に `'1h'` 追加 ([apps/dex_bot/domain/model/types.py](../apps/dex_bot/domain/model/types.py))
   - `TIMEFRAME_TO_SECONDS` に 1h 追加 (dex_bot, gmo_bot 両方の utils/time.py)
   - `Pair` literal に `BTC/JPY`, `ETH/JPY` 追加 ([apps/gmo_bot/domain/model/types.py](../apps/gmo_bot/domain/model/types.py))
   - `PAIR_SYMBOL_MAP` 拡張 ([apps/gmo_bot/adapters/symbol_map.py](../apps/gmo_bot/adapters/symbol_map.py))
   - GMO_COIN の `infer_broker` / `get_provider` で BTC/JPY, ETH/JPY を許容 ([research/src/data/source_registry.py](../research/src/data/source_registry.py))
   - `pair`, `signal_timeframe`, strategy 名↔timeframe 結合の schema 緩和 ([apps/gmo_bot/infra/config/schema.py](../apps/gmo_bot/infra/config/schema.py))
   - `fetch_ohlcv.py` の `--pair` choices 拡張
5. 既存テスト **314 / 314 pass**（schema/Pair 拡張後も regression なし）

## 1. P1-A/B/E: SOL/JPY 1h / 4h 軸

### 1.1 1h (windows=10, window_bars=1000 = 約 42 日/window)

データ: `research/data/raw/soljpy_1h_to_2026_05.csv` (10,766 bars)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| v0_baseline | -3.69 | +0.94 | 60.0 | REJECT |
| v2_default_bundle | -3.69 | +0.94 | 60.0 | REJECT |
| v2_D1_Volume_1.2x | -2.46 | +1.74 | 50.0 | REJECT |
| v2_B5+A4 | -3.69 | +0.94 | 60.0 | REJECT |
| supertrend_default | -10.10 | -2.43 | 30.0 | REJECT |
| donchian_default | -20.94 | -5.48 | 30.0 | REJECT |

**Done 基準 (pos_rate≥65 / mean≥+4 / min≥-5)**: 全 variant REJECT。
**観察**:
- 1h は SOL/JPY 15m baseline (mean +2.23) よりむしろ**悪化** (+0.94)
- v2_D1_Volume_1.2x は mean 最良 +1.74 だが pos_rate↓ 50%
- v0/v2_default/v2_B5+A4 が同一スコア → 行動を区別できる trade が ENG 発生していない
- Supertrend / Donchian は壊滅的（S2/S3/S4 と同様、1h でも回復せず）

### 1.2 4h (windows=8, window_bars=300 = 約 50 日/window)

データ: `research/data/raw/soljpy_4h_to_2026_05.csv` (2,724 bars)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| v0_baseline | 0.00 | 0.00 | 0.0 | REJECT (no trades) |
| v2_default_bundle | 0.00 | 0.00 | 0.0 | REJECT (no trades) |
| v2_D1_Volume_1.2x | 0.00 | 0.00 | 0.0 | REJECT (no trades) |
| v2_B5+A4 | 0.00 | 0.00 | 0.0 | REJECT (no trades) |
| supertrend_default | -5.58 | -2.59 | 12.5 | REJECT |
| donchian_default | -13.29 | -5.48 | 12.5 | REJECT |

**観察**: v0/v2 の strategy パラメータ (`long_atr_pct_max=0.7`, `max_distance_from_ema_fast_pct=0.9`) は 15m 振幅前提でチューニングされているため 4h では:

| guard | trip count (300 bars) |
| --- | ---: |
| `CHASE_ENTRY_TOO_FAR_FROM_EMA` | 56 |
| `SHORT_BREAKDOWN_NOT_CONFIRMED` | 52 |
| `RECLAIM_NOT_FOUND` | 51 |
| `LONG_ATR_REGIME_TOO_HOT` | 43 |

→ **取引が事実上発生しない**。フェアな評価には Phase 2 で param 再 tune が必須（4h ATR / EMA 距離は 15m の数倍）。

### 1.3 P1-A/B/E 結論

- **H1 (15m noise が悪い)**: 1h でも mean +0.94 で 15m baseline +2.23 より悪化。1h で edge が回復するという仮説は **支持されない**。4h は param 起因で検証不能。
- **trend detection 入れ替え (Supertrend / Donchian)**: 1h でも 15m と同様に壊滅的。S4 findings と一致。

## 2. P1-C/D: BTC/JPY / ETH/JPY 15m 軸

### 2.1 BTC/JPY 15m (windows=12, window_bars=3000 = 約 31 日/window)

データ: `research/data/raw/btcjpy_15m_1y.csv` (39,816 bars, 2025-03-27 〜 2026-05-21)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| v0_baseline | -6.12 | -0.59 | 25.0 | REJECT |
| v2_default_bundle | -6.12 | -0.59 | 25.0 | REJECT |
| v2_D1_Volume_1.2x | -10.73 | -0.54 | 25.0 | REJECT |
| v2_B5+A4 | -5.10 | -0.30 | 33.3 | REJECT |

### 2.2 ETH/JPY 15m (windows=12, window_bars=3000 = 約 31 日/window)

データ: `research/data/raw/ethjpy_15m_1y.csv` (39,777 bars, 2025-03-27 〜 2026-05-21)

| variant | min | mean | pos_rate% | verdict |
| --- | ---: | ---: | ---: | --- |
| v0_baseline | -14.99 | -2.97 | 33.3 | REJECT |
| v2_default_bundle | -14.99 | -2.97 | 33.3 | REJECT |
| v2_D1_Volume_1.2x | -13.18 | -0.36 | 50.0 | REJECT |
| v2_B5+A4 | -7.25 | -1.94 | 25.0 | REJECT |

### 2.3 P1-C/D 結論

- **H2 (SOL/JPY pair-specific decay)**: 完全に**否定**。
  - BTC/JPY 15m mean -0.59 (SOL +2.23 より大幅に悪い)
  - ETH/JPY 15m mean -2.97 (SOL より極めて悪い)
- 別 pair でも EMA pullback 構造に edge が無いことが確定 → **H3 (構造そのものが終わり)** を強く支持

## 3. 計画書 §2.2 撤退条件との照合

撤退条件: 「P1-A〜E **全てで** rolling mean < v0 baseline (+2.23) + 1pt」 AND 「rolling min が**全て** -5% より悪い」

| 軸 | mean | < +3.23? | min | < -5%? |
| --- | ---: | --- | ---: | --- |
| SOL/JPY 1h v0 | +0.94 | ✓ | -3.69 | ✗ (-3.69 > -5) |
| SOL/JPY 1h v2_D1_Volume | +1.74 | ✓ | -2.46 | ✗ |
| SOL/JPY 4h v0 | 0.00 | ✓ | 0.00 | ✗ (no trades) |
| BTC/JPY 15m v0 | -0.59 | ✓ | -6.12 | ✓ |
| ETH/JPY 15m v0 | -2.97 | ✓ | -14.99 | ✓ |
| SOL/JPY 1h supertrend | -2.43 | ✓ | -10.10 | ✓ |
| SOL/JPY 1h donchian | -5.48 | ✓ | -20.94 | ✓ |

- **mean 撤退条件**: 全軸で **+3.23 未満** → 全条件成立 ✓
- **min 撤退条件**: SOL/JPY 1h は -5% より良い (撤退条件には未到達) ✗ — ただし pos_rate は 50-60% で Phase 1 Done 基準 (pos_rate≥65) を満たさず

**判定**: 厳密な「全条件 AND」では撤退条件 borderline (1h の min が条件をぎりぎりクリア)。ただし Done 基準（採択側）にも全 variant が達していない。**Phase 2 採択候補ゼロ**。

## 4. 意思決定マトリクス (§3) との対応

> Phase 1 結果が「全滅」→ Phase 3 (代替系統)

**該当**: timeframe / pair / trend detection いずれの軸でも採択候補ゼロのため、計画書 §3 マトリクスでは **Phase 3 直行** が妥当。

### 4.1 留意点

1. **SOL/JPY 1h は最も「マシ」な軸**: mean +1.74 (v2_D1_Volume), pos_rate 60% (v0)。完全な全滅とは言えず、param 再 tune で改善余地が*ある可能性*はゼロではない。
2. **4h は param 不整合で no-trade**: 真のフェアな評価には 4h 用 param sweep が必要。Phase 2 直行する場合のリスク。
3. **BTC/ETH は ATR レベルが全く違う**: BTC/JPY price レベル ~12M JPY、`max_loss_per_trade_pct` や `min_notional` の絶対値解釈が違う可能性。LIVE 移行時には別 config が必須。

## 5. Phase 2 vs Phase 3 推奨

計画書 §3 マトリクスでは **Phase 3 直行** が文字通りの帰結。一方で:

- Phase 1 §2.2 の Phase 2 採択 Done 基準 (pos_rate≥65 / min≥-5 / mean≥+4) はかなり厳しい
- 4h param 再 tune は未実施
- SOL/JPY 1h `v2_D1_Volume_1.2x` は mean +1.74 / min -2.46 と 4 軸中で唯一目立つ

**推奨**:

- **A**: 計画書通り **Phase 3 (代替系統)** 直行。trend-following を諦め、Mean reversion (P3-V) または Grid (P3-G) に移行
- **B**: 妥協案として **Phase 2-A** で SOL/JPY 1h param 再 tune を 1 週限定で試行 (`long_atr_pct_max` / `max_distance_from_ema_fast_pct` を 1h 振幅に合わせる) → ダメなら Phase 3。Phase 1 投資の延長線として安価
- **C**: 計画書 §9 オプション A/B (縮退運用 / 完全撤退)

判断は計画書 §4 リソース見積 (Phase 3 は 80-160h) と現実的な ROI 期待値による。Phase 2-A の追加 1 週は安価なので **B 推奨** だが、計画書の機械的解釈では A。

## 6. 出力ファイル

- `research/data/runs/phase1_axis_sweep/soljpy_1h_all.json`
- `research/data/runs/phase1_axis_sweep/soljpy_4h_all.json`
- `research/data/runs/phase1_axis_sweep/btcjpy_15m.json`
- `research/data/runs/phase1_axis_sweep/ethjpy_15m.json`

## 7. 既存テスト

- 314 / 314 pass (Phase 1 のスキーマ緩和 / Pair 追加後も regression なし)
