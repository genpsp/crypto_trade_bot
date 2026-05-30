# gmo_bot component framework 設計

- 対象: `apps/gmo_bot/domain/strategy/components/` の 5層 component 設計
- 位置づけ: 戦略ロジックを config 駆動で組み替えるための抽象化レイヤの設計記録。**探索の結果・採否は [gmo_bot_exploration_findings.md](gmo_bot_exploration_findings.md) を参照**（本ファイルは設計のみを残す。旧計画書の探索ロードマップ/撤退基準/付録バグ記録は 2026-05-30 に findings へ集約・削除）。

## 1. 背景（なぜ component 化したか）

パラメータ探索で判明した、ロジック改修を要する主因:

- bimodal exit が脆い（全 trade が -1R か +2.2R、break-even margin が薄く WR 3pt 低下で赤字）
- chop regime で即反転 SL を量産（pullback→reclaim が chop で破綻）
- trend filter のエッジ decay（EMA9/34 cross の trend 定義に賞味期限）
- regime tag は trade に付くが gate に regime 情報を渡せていない

→ 値の最適化では限界。exit / regime / signal を**構造ごと差し替え可能**にするため、直線的な evaluate 関数を下記 5層に分離した。

## 2. 検証アーキテクチャ（component 設計）

```
┌────────────────────────────────────────────────────────────────┐
│  evaluate_strategy_for_model(direction, bars, config)          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ RegimeGate.allow(bars, config) -> bool / state          │  │  ← regime / sizing
│  ├──────────────────────────────────────────────────────────┤  │
│  │ EntrySignal.evaluate(bars, config) -> Decision          │  │  ← trend / entry
│  ├──────────────────────────────────────────────────────────┤  │
│  │ StopPolicy.initial_stop(decision, bars, config) -> stop │  │  ← stop
│  ├──────────────────────────────────────────────────────────┤  │
│  │ ExitPolicy.update(position, bar, config) -> Action      │  │  ← exit
│  │   - BE_STOP, PARTIAL_TP, TRAIL, TIME_EXIT, HOLD          │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ SizingPolicy.size_multiplier(state, config) -> float    │  │  ← sizing
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 実装パッケージ

| パッケージ | 役割 |
| --- | --- |
| `components/regime_gates.py` | `ADXGate` / `DonchianWidthGate` / `EquityFilterGate` / `SessionGate` / `VolumeConfirmedGate` / `ATRPctRangeGate` / `DirectionalSessionGate` / `BtcMomentumGate` / `CompositeRegimeGate` |
| `components/entry_signals.py` | `EmaPullbackSignal` / `SupertrendSignal` / `DonchianBreakoutSignal`（既存 evaluate を分解） |
| `components/stop_policies.py` | `SwingLowStop` / `ChandelierStop` / `FixedAtrStop` |
| `components/exit_policies.py` | `FixedRExit` / `PartialTpExit` / `TrailingExit` / `TimeExit` / `BreakEvenExit` / `CompositeExit`（per-bar ループは engine 側） |
| `components/sizing_policies.py` | `FixedSize` / `VolTargetSize` / `LossStreakSize` |
| `components/bundle.py` | 上記の factory 登録と `resolve_strategy_bundle`（config から組み立て） |

### 2.2 互換性

- 既存 model `ema_trend_pullback_15m_v0` は `EntrySignal=EmaPullbackSignal, StopPolicy=SwingLowStop, ExitPolicy=FixedRExit, RegimeGate=None, SizingPolicy=AtrRegimeMultiplier` の組合せで**byte-level 再現**できることを reproduction test で担保（314 テスト pass）。
- 新規 model は `strategy.name = "ema_trend_pullback_15m_v2"` 等の bundle 名で registry に登録。
- Firestore config の `strategy.components` キーで個別 policy を config 駆動で差し替え可能。

### 2.3 backtest_engine 改修ポイント（per-bar ExitPolicy）

entry 時に stop/TP を fix して touch 判定するだけの旧構造を、**per-bar で `ExitPolicy.update(position, bar)` を呼ぶループ**に変更:

```python
for bar in bars_after_entry:
    action = exit_policy.update(position, bar, atr_at_entry, ...)
    match action:
        case BREAK_EVEN: position.stop_price = position.entry_price
        case TRAIL(new_stop): position.stop_price = new_stop
        case PARTIAL_TP(fraction, price): close_partial(...)
        case CLOSE(price, reason): close_full(...)
        case HOLD: continue
    # touch 判定 (stop / TP) は従来通り
```

→ BE / Time / Partial / Trailing 等を policy 差し替えだけで実装できる。
→ LIVE 側 `apps/gmo_bot/infra/execution/exit_order_monitor.py` も同じ ExitPolicy を呼ぶことで backtest と exit ロジックを共有（**shadow_compare の前提条件**）。

## 3. 既存ロジックの依存関係マップ

`evaluate_ema_trend_pullback_15m_v0`（728行）の関心事と分解先。新 entry signal を起こす際の参照用:

| 行範囲 | 関心事 | 分解先 |
| --- | --- | --- |
| 294-321 | market context (EMA / closes / highs / lows) | `EmaPullbackSignal._build_context` |
| 322-441 | upper timeframe trend gate (4h EMA cross, gap, slope) | `UpperTrendGate` |
| 442-499 | EMA filter + pullback detection | `EmaPullbackSignal.evaluate_pullback` |
| 501-557 | reclaim/breakdown confirm + distance from EMA | `EmaPullbackSignal.evaluate_reclaim` |
| 559-601 | RSI gate | `RsiGate` |
| 603-661 | ATR + size multiplier + stop | `AtrSizing` + `SwingLowStopPolicy` |
| 663-712 | TP calculation | `FixedRExitPolicy` |
