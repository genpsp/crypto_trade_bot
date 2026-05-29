# dex_bot v2 戦略フレームワーク移植指示書

> **目的**: gmo_bot で 2026-05-22 に LIVE cutover した v2 戦略フレームワーク（commit `28afdfd`）を dex_bot にも移植する手順を定義する
>
> **前提**: bar-fetch 不足バグ修正（[apps/dex_bot/domain/strategy/registry.py](../apps/dex_bot/domain/strategy/registry.py) の `resolve_required_history_bars`）は移植済み
>
> **背景**: dex_bot は schema レベルでは既に `ema_trend_pullback_15m_v2` / `supertrend_15m_v0` / `donchian_breakout_15m_v0` / `mean_reversion_15m_v0` を受け入れているが、registry が dispatch していないため設定すると **silent に別戦略（`ema_trend_pullback_v0`）にフォールスルーする** 状態である

---

## 0. TL;DR

```
Phase 1: variant_id プラミング + v2 dispatch（最小実装、LIVE で v0 と完全互換）
Phase 2: supertrend / donchian / mean_reversion を dispatch 追加（schema 整合）
Phase 3: components/ レイヤを共有化、dex_bot 経由でも resolve_strategy_bundle が使えるようにする
Phase 4: LIVE cutover 用 runbook を別途整備
```

Phase 1 だけで「`ema_trend_pullback_15m_v2` を設定しても安全に動く」状態になる。Phase 2 / 3 は backtest や検証用途で必要になったタイミングで進めればよい。

---

## 1. 現状ギャップ調査（2026-05-29 時点）

### 1.1 gmo_bot にあって dex_bot にないもの

| 領域 | gmo_bot | dex_bot |
|---|---|---|
| registry dispatch (`ema_trend_pullback_15m_v2`) | ✓ ([apps/gmo_bot/domain/strategy/registry.py:73-81](../apps/gmo_bot/domain/strategy/registry.py#L73-L81)) | ✗ schema 許容のみで未実装 |
| registry dispatch (`supertrend_15m_v0` / `donchian_breakout_15m_v0` / `mean_reversion_15m_v0`) | ✓ | ✗ schema 許容のみで未実装 |
| `meta.variant_id` schema validation | ✓ ([apps/gmo_bot/infra/config/schema.py:130-134](../apps/gmo_bot/infra/config/schema.py#L130-L134)) | ✗ `meta.note` のみ |
| `TradeRecord.variant_id` フィールド | ✓ ([apps/gmo_bot/domain/model/types.py:172](../apps/gmo_bot/domain/model/types.py#L172)) | ✗ |
| `open_position` で variant_id を snapshot | ✓ ([apps/gmo_bot/app/usecases/open_position.py:142](../apps/gmo_bot/app/usecases/open_position.py#L142)) | ✗ |
| `components/` フレームワーク（base / bundle / regime_gates / exit_policies / stop_policies / sizing_policies）| ✓ | gmo_bot 配下に存在し共用中 |

### 1.2 既に dex_bot で取り込まれているもの

- schema の戦略 whitelist に v2 / supertrend / donchian / mean_reversion は登録済み（[apps/dex_bot/infra/config/schema.py:68-74](../apps/dex_bot/infra/config/schema.py#L68-L74)）
- risk モジュール 3 つは v0/v2 両方を strategy name set に登録済み
  - [apps/dex_bot/domain/risk/short_regime_guard.py:8](../apps/dex_bot/domain/risk/short_regime_guard.py#L8)
  - [apps/dex_bot/domain/risk/loss_streak_trade_cap.py:5](../apps/dex_bot/domain/risk/loss_streak_trade_cap.py#L5)
  - [apps/dex_bot/domain/risk/short_stop_loss_cooldown.py:8](../apps/dex_bot/domain/risk/short_stop_loss_cooldown.py#L8)
- `resolve_required_history_bars` で v0/v2 ともに 600 bar 取得（2026-05-29 移植済）

### 1.3 cross-app 依存関係の現状

gmo_bot の v0 / v2 evaluator および components/ は既に `apps.dex_bot.domain.model.types` / `apps.dex_bot.domain.risk.swing_low_stop` / `apps.dex_bot.domain.strategy.shared.*` に依存している（つまり dex_bot 側が「共通基盤」になっている）

- [apps/gmo_bot/domain/strategy/components/base.py:12](../apps/gmo_bot/domain/strategy/components/base.py#L12)
- [apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py:8](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py#L8)
- [apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v2.py:14](../apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v2.py#L14)

このため Phase 1/2 では **新規ファイルをほぼ作らず、dex_bot の registry から gmo_bot の evaluator を import するだけ** で済む（既存の v0 と同じパターン）

---

## 2. Phase 1: variant_id プラミング + v2 dispatch

### 2.1 目的

`strategy.name = "ema_trend_pullback_15m_v2"` を設定したとき、LIVE bot が

1. 600 bar を取得し（移植済）
2. v0 と完全に同じシグナルロジックで判定し（v2 は v0 delegate）
3. trade を記録する際に `variant_id` をラベルとして残す

状態を達成する。これだけで「設定だけで A/B 切り替えできる」最小成立形となる

### 2.2 変更ファイル

| ファイル | 変更内容 |
|---|---|
| [apps/dex_bot/domain/strategy/registry.py](../apps/dex_bot/domain/strategy/registry.py) | `apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v2` から evaluator を import し、`evaluate_strategy_for_model` に dispatch 分岐を追加 |
| [apps/dex_bot/domain/model/types.py](../apps/dex_bot/domain/model/types.py) | `MetaConfig` に `variant_id: NotRequired[str]` 追加、`TradeRecord` に `variant_id: str` 追加 |
| [apps/dex_bot/infra/config/schema.py](../apps/dex_bot/infra/config/schema.py) | `_parse_meta` 系で `meta.variant_id` を optional 検証（gmo の [schema.py:130-134](../apps/gmo_bot/infra/config/schema.py#L130-L134) と完全に同じパターン）、`meta` 出力辞書にも条件付き含める |
| [apps/dex_bot/app/usecases/open_position.py](../apps/dex_bot/app/usecases/open_position.py) | TradeRecord 生成時に `"variant_id": str(config["meta"].get("variant_id", "") or "")` を追加（gmo の [open_position.py:139-142](../apps/gmo_bot/app/usecases/open_position.py#L139-L142) を移植）|

### 2.3 移植元コードの参照

- registry dispatch パターン: [apps/gmo_bot/domain/strategy/registry.py:73-81](../apps/gmo_bot/domain/strategy/registry.py#L73-L81)
- variant_id 型定義: [apps/gmo_bot/domain/model/types.py:32-38](../apps/gmo_bot/domain/model/types.py#L32-L38)、[172 行付近](../apps/gmo_bot/domain/model/types.py#L172)
- schema 検証: [apps/gmo_bot/infra/config/schema.py:130-157](../apps/gmo_bot/infra/config/schema.py#L130-L157)
- open_position snapshot: [apps/gmo_bot/app/usecases/open_position.py:131-142](../apps/gmo_bot/app/usecases/open_position.py#L131-L142)

### 2.4 テスト

- 新規: `tests/test_dex_strategy_registry.py` に `evaluate_strategy_for_model` で `strategy.name = "ema_trend_pullback_15m_v2"` を渡したときの dispatch テストを追加（既に分岐が増えるので分岐網羅）
- 新規: `tests/test_dex_open_position.py`（存在しなければ新設）で、`meta.variant_id` 有り / 無し両ケースで `TradeRecord.variant_id` が `""` または該当値で snapshot されることを検証
- 新規: `tests/test_dex_config_schema.py`（存在しなければ新設）で `meta.variant_id` の optional 検証ケース（空文字 NG / 文字列 OK / 省略 OK）

合格基準: 既存テスト全件 pass + 新規追加分も pass

### 2.5 backward compatibility

- 既存 LIVE 設定（`meta.variant_id` なし）は影響ゼロ（schema は optional、TradeRecord は `""` で snapshot）
- `TradeRecord.variant_id = ""` は「v2 era 以前の trade」を示す慣例とする（gmo_bot と同じ）

---

## 3. Phase 2: 残り戦略の dispatch 追加

### 3.1 目的

schema が既に許容している `supertrend_15m_v0` / `donchian_breakout_15m_v0` / `mean_reversion_15m_v0` の **silent fallthrough を解消** する

現状 dex_bot で `supertrend_15m_v0` を設定すると、schema validation を通過したあと registry の最終フォールスルーで `evaluate_ema_trend_pullback_v0` が呼ばれ、しかも `direction != "LONG"` なら ValueError で死ぬ。本質的に **誤った戦略が走る危険な状態** なので、Phase 1 とセットで実施するのが望ましい

### 3.2 選択肢

| 選択肢 | 説明 | 推奨 |
|---|---|---|
| A) gmo_bot 配下の evaluator を import して dispatch | Phase 1 と同じパターン。新規ファイルゼロ | ◎（最小コスト）|
| B) schema から戦略 whitelist を削減 | 「dex_bot ではこの戦略を扱わない」と決め、schema から `supertrend_15m_v0` 等を除去 | △（将来の選択肢を狭める）|

選択肢 A を推奨する

### 3.3 変更ファイル（選択肢 A の場合）

| ファイル | 変更内容 |
|---|---|
| [apps/dex_bot/domain/strategy/registry.py](../apps/dex_bot/domain/strategy/registry.py) | `apps.gmo_bot.domain.strategy.models.{supertrend_15m_v0, donchian_breakout_15m_v0, mean_reversion_15m_v0}` から import し dispatch 追加 |

### 3.4 テスト

- `tests/test_dex_strategy_registry.py` に 3 戦略分の dispatch 確認テスト追加

### 3.5 cross-app 依存についての判断ポイント

「dex_bot が gmo_bot 配下の戦略実装を直接 import している」状態は既存（v0 から）の構造を踏襲するだけだが、長期的には以下のいずれかへの移行を検討すべき

- (i) `apps/shared/strategy/models/` 等に移動して両 bot から import
- (ii) dex_bot の戦略は `apps/dex_bot/domain/strategy/models/` に明示的にコピー
- (iii) 現状維持（gmo_bot を共通実装場所として扱う）

判断は Phase 3 完了後または「dex_bot 専用ロジックが必要になったとき」に行う。Phase 1-2 では現状維持で進める

---

## 4. Phase 3: components/ フレームワーク共有化（オプション）

### 4.1 目的

dex_bot の backtest（[research/src/domain/backtest_engine.py:200-202](../research/src/domain/backtest_engine.py#L200-L202)）で `resolve_strategy_bundle` を呼べるようにし、Phase 1 で導入した v2 設定の `strategy.components` フィールドが engine 側で反映されるようにする

注意: components/ は **LIVE 側 run_cycle では消費されていない**（gmo_bot も同様）。Phase 1 だけ完了していれば LIVE 動作は v0 互換で完結する

### 4.2 変更ファイル

LIVE 用変更はなし。research/ 側の検証ロジックを dex_bot bar データに対しても動かしたい場合に必要となる

| ファイル | 変更内容 |
|---|---|
| [research/src/domain/backtest_engine.py](../research/src/domain/backtest_engine.py) | dex_bot 由来の config を入力したときも `_strategy_uses_component_bundle` が True になる経路を確認・追加 |
| `research/scripts/` 配下 | dex_bot 用 OHLCV と config を受け取れる薄い CLI 追加（必要に応じて）|

### 4.3 判断ポイント

Phase 3 は「dex_bot 専用の v2 戦略探索を始めるとき」または「dex_bot trade を post-mortem 分析するとき」に着手する。それまでは未着手で問題なし

---

## 5. Phase 4: LIVE cutover runbook（着手前判断）

dex_bot で実際に LIVE 切り替えを行う段階で、[docs/runbook/gmo_v2_cutover.md](runbook/gmo_v2_cutover.md) を雛形に `docs/runbook/dex_v2_cutover.md` を作成する

雛形のうち dex_bot 固有で書き換える項目

- 設定の保存先（dex_bot は Firestore か json か）
- archive 先のパス
- kill-switch 基準値（dex_bot のヒストリカル PnL 分布に合わせて）
- rollback 手順
- `dump_live_trades_for_profile.py` に相当する dex_bot 用スクリプトの有無

着手は Phase 1-2 完了 + dex_bot 用 backtest で edge を確認してから

---

## 6. 実装順序（推奨）

```
1. Phase 1 (variant_id + v2 dispatch)
   ├── apps/dex_bot/domain/model/types.py
   ├── apps/dex_bot/infra/config/schema.py
   ├── apps/dex_bot/app/usecases/open_position.py
   ├── apps/dex_bot/domain/strategy/registry.py
   └── tests/ 新規 3 ファイル
2. Phase 2 (残り戦略 dispatch)
   └── apps/dex_bot/domain/strategy/registry.py に dispatch 追加 + test
3. (任意) Phase 3
4. (LIVE 切替時) Phase 4
```

各 Phase 単位で 1 commit 推奨。commit message 雛形は gmo_bot 側の `28afdfd` / `cd5b5a8` を参考にする

---

## 7. 回帰防止チェックリスト

実装後に最低限以下を確認

- [ ] `python -m unittest discover -s tests` で全件 pass（事前から失敗の `test_research_store` 2 件を除く）
- [ ] dex_bot で `strategy.name = "ema_trend_pullback_15m_v2"` を設定した bot を dry-run し、`_resolve_ohlcv_limit` が 600 を返すこと
- [ ] 同設定で trade を 1 件発生させ、TradeRecord に `variant_id` が含まれること
- [ ] schema validation で `meta.variant_id` 空文字 / 非文字列が拒否されること
- [ ] 既存 LIVE 設定（`variant_id` フィールドなし）で起動できること
- [ ] cross-app import が増えていることを README / CLAUDE.md に追記（将来の構造判断のため）

---

## 8. 参考

- gmo_bot 側 cutover の経緯: [docs/gmo_bot_post_kill_postmortem_findings.md](gmo_bot_post_kill_postmortem_findings.md)
- v2 framework の設計意図: [docs/gmo_bot_logic_exploration_plan.md](gmo_bot_logic_exploration_plan.md) §2
- bar-fetch 不足インシデント（移植済バグ）: gmo_bot commit `cd5b5a8`
- gmo_bot LIVE cutover runbook: [docs/runbook/gmo_v2_cutover.md](runbook/gmo_v2_cutover.md)
