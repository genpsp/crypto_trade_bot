# v2 LIVE 改善の調査と計画（2026-06-10）

- 対象: 現行 LIVE `v2_dir_session_vol_time120`（[current.json](../research/models/gmo_ema_pullback_15m_both_v0/config/current.json)）そのものの改善。新 edge 探索ではない（そちらは [direction_2026-06](gmo_bot_direction_2026-06.md)）。
- 調査ソース: live 17 trade 実績（[v2_live_2026-05-22_2026-06-03.json](../research/data/execution_profiles/raw_trades/v2_live_2026-05-22_2026-06-03.json)）の trigger vs fill 実測、SOL_JPY L2 板（[orderbook/](../research/data/raw/orderbook/)）の impact 実測、postmortem/phase2 の検証履歴棚卸し。

## 1. 調査で確定した事実

### 発見①: entry に stale-signal 執行事故 — 17 trade 中 2 件が bar close から 5/10 分遅延で +109/+129bps slip

| 計測（trigger vs fill, 正=不利） | median | mean | 最大 |
| --- | ---: | ---: | ---: |
| entry slip | **+3.1bps** | **+16.1bps** | +129.0bps |
| TP exit slip | -0.1bps | -0.4bps | +5.9bps |
| SL exit slip | +3.5bps | +7.3bps | +33.2bps |

- 通常 entry は bar close の **2-3 秒後**に執行（backtest 想定 slippage 3bps と median は整合 ✓）。
- しかし 2026-05-28（bar 03:15 → entry 03:20:02）と 2026-06-02（bar 14:15 → entry 14:25:03）は**ちょうど 5 分/10 分 + 2-3 秒の遅延**。下落中の SHORT で trigger より 109/129bps 低く売り、これだけで **mean を 3.1→16.1bps に押し上げ（≈ +14bps/trade のドラッグ ≒ v2 の per-trade 経済性 ~20bps の 7 割）**。
- scheduler は毎分実行（[cron_cycle.py](../apps/gmo_bot/infra/scheduler/cron_cycle.py)）なので周期遅れではない。サイクル失敗 or 市場データ遅延の後、後続サイクルが同じ closed bar を拾い直して**鮮度チェックなしに stale な signal を執行**した形（[run_cycle.py](../apps/gmo_bot/app/usecases/run_cycle.py) は `get_last_closed_bar_close` で bar を引き直すが bar 経過時間のガードが無い）。根本原因（該当時間帯に 03:16-03:19 のサイクルが何をしていたか）は live ログで要確定。

### 発見②: サイズ容量は現行の 30-80 倍 — 最大の PnL レバーは戦略でなく資金

SOL_JPY レバレッジ板 L2（78 snap, 2026-06-07）で成行サイズ別 impact（mid 比, depth10 喰い上げ）:

| 成行サイズ | 想定 notional | median impact | p90 |
| ---: | ---: | ---: | ---: |
| 0.6 SOL（現行） | ¥6千 | +3.4bps | +9.6bps |
| 5 SOL | ¥5万 | +3.4bps | +9.8bps |
| 20 SOL | ¥20万 | +3.9bps | +10.6bps |
| 50 SOL | ¥51万 | +5.2bps | +11.0bps |

- impact の支配項は half-spread（~3.4bps）であり深さではない。**¥20万ポジションまで実質ノーコスト（+0.5bps）、¥50万でも +1.8bps**。
- 現行ポジション ~¥8 千は口座資金そのものが制約（leverage 1.0 × margin 0.99）。さらに小口ゆえ**手数料の円単位切り上げ（¥3/leg ≈ 3.7bps、本来 3bps）と partial fill 毎の切り上げで過払い** — サイズアップで自然解消。
- 留保: 78 snap・日曜・低 vol 帯の計測。平日・signal 発生時間帯（JST 深夜/朝）の depth を追加収集して確認が要る（収集スクリプトは稼働済み）。

### 発見③: TP 執行はすでにほぼ理想、exit 側の maker 化の期待値は下方修正

- TP exit slip median **-0.1bps** — 価格面の改善余地はほぼゼロ。resting limit 化で残るのは **taker fee 3bps の節約のみ**（[direction doc](gmo_bot_direction_2026-06.md) B-Step1 の期待値 ~3-4bps/trade を「TP 約定 trade あたり ~3bps」に下方修正）。
- SL は exchange STOP 注文（[gmo_margin_execution.py](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py)）で slip +3.5bps median — 保護注文として妥当、現状維持。

### 発見④: v2 の edge の検証窓は 15 ヶ月しかない（GMO 履歴の上限）

- v2 採用根拠は GMO SOL/JPY 2025-02〜2026-05 の 13 窓のみ。**funding carry を殺した「期間固有」リスク（2.6 年拡張で消失）と同型の検証が dir×hour には未実施**。
- GMO データはこれ以上遡れないが、**Binance SOLUSDT 15m 4 年分が取得済み**（[binance15m/](../research/data/raw/binance15m/)、Round 2 資産）— クロス venue での stationarity 検証が可能。
- 既知の弱点は記録済み: 直近窓 w12 -8.01、holdout（2026-03〜05 荒れレジーム）は baseline v2 自体が Gate A fail。現状の防御は 5-day kill-switch のみ。

## 2. 改善 Track（優先順）

### Track 1: stale-signal ガード（バグ修正級・即実施）

1. live ログで 05-28 03:15-03:20 / 06-02 14:15-14:25 のサイクル挙動を特定（skip 理由 or 失敗系列）
2. **鮮度ガード**: `run_at − bar_close > N 秒`（例 90s）なら当該 bar の entry を放棄（skip marker 記録）
3. **drift ガード**: 発注直前に ticker vs `entry_trigger_price` の乖離 > 閾値（例 30bps or 0.5×ATR）で abort — 鮮度ガードの取りこぼし（データ遅延時の高速市場）も防ぐ
- 効果: 確実に +~14bps/trade 相当の tail ドラッグ除去。backtest（bar close 直後 fill 前提）と live の整合性回復。
- Done: ガード発火が skip ログに乗ること + 以後の entry slip 分布で mean ≈ median（tail 消滅）。

### Track 2: dir×hour edge の stationarity 検証（research ≤1 日・Track 3 のゲート）

- Binance SOLUSDT 15m 4y で v2 を gate 有無比較（既存 sweep ハーネス + binance15m データ。USDT 建てなので絶対値でなく **gate on/off の差分**を見る）。サブ期間 2022-23 / 2024 / 2025+ 分解。
- 解釈の pre-register:
  - **SOLUSDT にも存在し 4y 安定** → 構造的（SOL のグローバルな時間帯フロー）。Track 3 サイズアップの根拠強化
  - **2025+ のみ** → 期間固有の疑い濃厚。サイズ抑制・kill-switch 死守、decay 週次トラッキングを強化
  - **SOLUSDT に無い** → JPY/GMO 固有（JST retail flow・USDJPY セッション）。否定材料ではないが長期確認は不能と確定 → サイズアップは保守的に
- 併せて live decay トラッキングを週次運用に: live trade 蓄積で direction×hour バケット WR を backtest 期待値と比較（n が貯まるまでは参考値）。

### Track 3: サイズアップ（最大の PnL レバー・要資金判断）

- 板容量は ~¥20-50 万ポジションまで開いている（発見②）。**ボトルネックは口座資金**であり、これは bot の改善でなく資金配分の意思決定。
- 前提: Track 1 完了（執行事故の根絶）+ Track 2 の結果が「2025+ のみ」でないこと + 平日板の追加計測。
- 手順: kill-switch 体制下で段階的（×5 → 様子見 2 週 → ×20）。スリッページ実測を各段階で取り、impact 曲線（発見②）との整合を確認してから次段へ。
- 副次効果: 手数料切り上げ過払いの解消（小口ペナルティ消滅）。

### Track 4: 執行の maker 化（bounded・direction doc の B と同一インフラ）

- **TP の resting limit 化**: 期待値 ~3bps/TP-trade（fee のみ、発見③）。リスクほぼゼロだが効果も小 — post-only 執行モジュール（direction doc A/B 共用）を作るついでに実施
- **entry の maker 化 PoC**: signal 後 touch に SOK limit。directional 戦略の limit entry は**約定が loser に偏る selection bias** を持つため、fill 率でなく filled vs missed の条件付き PnL で判定。entry median slip は +3.1bps と想定内なので、節約上限は ~6bps/側（fee 3 + half-spread ~3）− 機会損失。
- 優先度を Track 1-3 より下げる根拠: 発見①③より、執行の主要な毀損は spread でなく tail 事故であり、それは Track 1 が潰す。

### Track 5: パラメタ頑健性の確認（single shot・チューニングはしない）

- session 境界 ±1-2h 摂動・volume 0.4 近傍・TP 1.8R / ATR 1.5x（v0 から未再調整）の**プラトー確認のみ**。プラトーなら現状維持、崖なら過適合警告としてサイズ判断に反映。
- 同じ 13 窓での再 tuning は overfit 一直線なので**最適値の更新はしない**（pre-register）。
- funding tail filter（Track D: +0.16〜0.51pt の marginal+）は見送り判断を維持。

## 3. 再訪防止 — v2 の上で試して劣化が確定済みの系統

phase2/postmortem の in-engine 検証で v2（mean +5.57）に追加すると劣化したもの（[directional.json](../research/data/runs/phase2_validation/directional.json) / [dir_exit_combos.json](../research/data/runs/phase2_validation/dir_exit_combos.json) / [postmortem_v0/](../research/data/runs/postmortem_v0/)）:

| 追加要素 | 結果（rolling mean） | 備考 |
| --- | --- | --- |
| + btc_momentum gate | +5.47 → +2.88 | 単独 +3.15 でも v2 比劣後。U字 discriminator は gate 化で死ぬ |
| + BreakEven 1R | +5.47 → +2.17 | exit 系は time120 のみ正解 |
| + Partial TP 1R | +5.47 → +2.69 | min は改善するが mean 半減 |
| + equity curve gate | → 負（-0.84〜-0.99） | 全滅 |
| + ATR gate（単独/併用） | +1.26〜+1.45 | v0 既存 `long_atr_pct_max` と二重作用 |
| volume multiplier 再調整 | 0.3-0.8 でフラット | 0.4 はプラトー上 — 触る価値なし |

## 4. 一行結論

**v2 のロジック（gate/exit）はやり尽くされており追加で勝てる要素は無い。改善余地はロジック外に集中している: (1) stale-signal 執行事故の根絶（+14bps/trade 相当・バグ修正級）、(2) 15 ヶ月しかない検証窓のクロス venue 補強、(3) その結果次第で 30-80 倍開いている板容量へのサイズアップ。執行の maker 化は実測の結果、当初想定より小粒（TP は既にほぼ理想執行）。**
