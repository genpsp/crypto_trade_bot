# SOL/JPY 15m 撤退後の戦略探索計画

- 前提: [docs/gmo_bot_logic_exploration_plan.md](gmo_bot_logic_exploration_plan.md) §4 計画全体 kill criterion に該当（詳細は [S4 総括](gmo_bot_logic_exploration_s4_findings.md) に集約。S2/S3 個別メモは 2026-05-22 に整理済み）
- スコープ: SOL/JPY 15m + EMA pullback 系統を捨てた前提で、**どの軸の変更が edge を取り戻すか**を最短コストで特定する
- 制約: LIVE は size 0.5x / LONG-only に縮退して並行運用（出血止血）。本探索はそれと独立に進む

## 0. 何がわかっていて、何がわかっていないか

### 0.1 確定事項（S2〜S4 で証明済）

- SOL/JPY 15m + EMA9/34 pullback + fixed-R exit は **Done 基準を満たせない**（rolling pos_rate ≤ 60% / min ≤ -3.16%）
- **exit policy / regime gate / entry filter の改修では mean PnL を +0.6pt より改善できない**
- **trend detection 入れ替え (Supertrend/Donchian) は壊滅的に劣化** (8 variant 全て train PnL 30% 未満)
- → 「signal そのもの」「市場そのもの」のどちらか、または両方を変えないと改善余地は無い

### 0.2 未確定事項（本計画で検証する）

| 仮説 | 検証コスト | 期待される値 |
| --- | --- | --- |
| H1: 15m bar の noise が問題（signal は OK） | **小** | 1h/4h で v0 復活なら確定 |
| H2: SOL/JPY が問題 (pair-specific edge decay) | 中 | BTC/JPY や ETH/JPY 15m で v0 が改善するなら確定 |
| H3: EMA pullback 構造そのものが終わっている | 中 | H1, H2 共に失敗 → 構造変更必須 |
| H4: trend-following 全般が SOL/JPY 15m に向かない | 大 | 別系統 (grid / MM) で正の edge が出るかどうかで判明 |

## 1. ゴール

- **Done 基準 (継承)**: 計画書 §6 と同じ — pos_rate ≥ 90% / rolling min ≥ 0% / DSR p < 0.10 / break-even margin ≥ 6pt
- **本計画固有のゴール**:
  - 8 週以内に PAPER 30 日 → LIVE 0.5x に進めるロジック・市場の組合せを **1 つ以上特定する**
  - もしくは「現行 SOL/JPY trading 自体を畳む」判断材料を提供する

## 2. Phase 構造（5 phases、累計 8〜12 週）

```
Phase 0: 並行運用    (本計画と独立、即実施)
   ↓
Phase 1: 低コスト falsification   (1 週)
   ↓ どこに entry edge が残っているか判明
Phase 2: targeted deep dive       (2〜4 週)
   ↓ Phase 1 勝者を完全に評価
Phase 3 (optional): 代替系統      (4〜6 週)
   ↓ Phase 2 で勝者が出なかった場合
Phase 4: 運用ロールアウト          (4 週)
```

### 2.1 Phase 0: 並行運用（即実施、本計画と独立）

operational tasks（本計画には直接含まれないが前提）:

- LIVE を size 0.5x / LONG-only に縮退 (現行 v0 のまま)
- LIVE / PAPER trade を Firestore から JSON dump して `research/data/execution_profiles/raw_trades/` に蓄積
- 100 trade 蓄積したら `build_execution_profile` で stochastic_v1 profile を実体化（A.2 で必須化済）
- shadow_compare の準備（LIVE と backtest の一致率測定）

**Done**: 出血が止まり、stochastic profile 用 LIVE データが蓄積されている状態

### 2.2 Phase 1: 低コスト falsification（1 週）

**仮説 H1〜H3 を最小コストで反証**する。1 週間で「どの軸を深掘るか」を決定する。

| Track | 内容 | 実装コスト | 期待される signal |
| --- | --- | --- | --- |
| P1-A | SOL/JPY 1h / 4h で v0 を rolling 評価 | 小（CSV を時間軸 resample）| v0 シグナルが noise 抜きで動くか |
| P1-B | SOL/JPY 1h / 4h で v2 + 全 S2-S4 勝者 構成を評価 | 小（既存スクリプト流用）| 上記改良版が長期足で活きるか |
| P1-C | BTC/JPY 15m で v0 を rolling 評価 | 中（OHLCV fetcher の broker adapter 追加）| pair-specific decay か |
| P1-D | ETH/JPY 15m で v0 を rolling 評価 | 中（同上） | 別 pair 一般化検証 |
| P1-E | SOL/JPY 1h で Supertrend / Donchian を rolling 評価 | 小 | trend detection が長期足で活きるか |

**実装メモ**:
- **P1-A/B/E**: 既存の `soljpy_15m_to_2026_05.csv` を pandas で 1h / 4h に resample → `read_bars_from_csv` 互換の CSV を出力。新規コードはほぼゼロ
- **P1-C/D**: `research/scripts/fetch_ohlcv.py` の broker 引数を拡張、または GMO API クライアント (`apps/gmo_bot/adapters/`) を流用して BTC/JPY / ETH/JPY OHLCV を取得
- 評価スクリプトは `explore_track_b_regime_gates.py` をテンプレに `--pair --timeframe` 引数を追加

**Phase 1 Done 基準（採択 = Phase 2 へ進む）**:

- 少なくとも 1 つの軸で v0 rolling mean ≥ +4% / pos_rate ≥ 65% / min ≥ -5%
- もしくは Supertrend/Donchian が rolling mean ≥ +3% / pos_rate ≥ 60%

**Phase 1 撤退条件（Phase 3 へ）**:

- P1-A〜E 全てで rolling mean < v0 baseline (+2.23) + 1pt
- かつ rolling min が全て -5% より悪い
- → trend-following 系統全般が SOL/JPY エコシステムで機能していない可能性大 → Phase 3 へ

### 2.3 Phase 2: targeted deep dive（Phase 1 勝者の場合のみ、2〜4 週）

Phase 1 の勝者 1 〜 2 候補について、**完全な走査**を行う。

| 勝者軸 | やること |
| --- | --- |
| 1h / 4h timeframe | 既存 v2 component bundle を当該 timeframe で full sweep。Track A/B/D の全候補を再走査（30,000 bar 相当の長期データ収集が必要） |
| 別 pair | broker / pair-specific 箇所を refactor（loss_streak_dynamic_cap などの strategy.name 固有処理含む）、その上で同じ sweep を走らせる |
| Supertrend on 1h | 既存 `supertrend_15m_v0` を `supertrend_1h_v0` として複製、ATR period などを再 tune |

**Phase 2 Done 基準（PAPER に進む）**:

| 基準 | 目標 |
| --- | --- |
| rolling 13 windows | pos_rate ≥ 80% / min ≥ -2% / mean ≥ +5% |
| holdout walk-forward 6 windows | 4+ positive / total +10%+ |
| stochastic_v1 + 実 profile seed p05 | positive |
| break-even WR margin | ≥ 5pt |

（計画書 §6 より緩めに設定 — 元計画の Done 基準は「完全に edge 確立」だが、ここでは PAPER 投入の閾値）

**Phase 2 撤退条件（Phase 3 へ）**:

- Done 基準 1〜2 個しか満たさない、または rolling min が改善しないなら別軸 / Phase 3 へ

### 2.4 Phase 3: 代替系統 (optional, Phase 1/2 で勝者が出なかった場合のみ、4〜6 週)

trend-following を諦め、別カテゴリーの strategy を試す。

| Track | 内容 | 実装コスト | 期待される性質 |
| --- | --- | --- | --- |
| P3-G | Grid trading (chop 重視) | 大（新規 engine、position 複数同時管理）| chop で edge、trend で limit hit |
| P3-M | Spread / market making (small order 連発) | 大（fee 精緻化、order placement model） | 高頻度・低 win 幅 |
| P3-O | Volatility harvesting (option-like, IV 売り) | 大（option データ取得、Greek 計算） | implied vol 環境次第 |
| P3-V | Mean reversion (chop 専用) | 中（既存 component に逆張り entry signal 追加）| Range 相場で edge |

**実装メモ**:
- P3-V は既存 v2 framework で実装可能（新 EntrySignal だけ追加）→ **Phase 3 内で最低コスト**
- P3-G は新規 engine 必須。並列 position / partial fill / multi-leg 管理の domain model が要る
- P3-M は order placement 精度に依存、シミュレーション精度が課題
- P3-O は GMO API で option が扱えるかから検証必要

**Phase 3 Done 基準**:

- 同 Phase 2 と同じ閾値
- 加えて max DD ≤ 5%（grid / MM は drawdown control が肝）

**Phase 3 撤退条件**:

- P3-G/M/O のいずれも Phase 2 Done 基準を満たさない
- → **bot 開発自体を一時停止**する判断材料を提供

### 2.5 Phase 4: 運用ロールアウト（4 週）

勝者構成について:

1. PAPER 30 日（stochastic profile + 実 LIVE 環境模倣）
2. LIVE 0.5x 30 日（実 LIVE で profile 補正 / shadow_compare）
3. LIVE 1.0x 移行

shadow_compare で LIVE vs backtest 一致率 ≥ 90% が前提（計画書 §5.1 に既出）。

## 3. 意思決定マトリクス

各 Phase の結果に応じた次のアクション:

```
                  Phase 1 結果
                  │
       ┌──────────┼──────────┬──────────┐
       │          │          │          │
   timeframe   pair        Supertrend  全滅
   勝ち        勝ち        on 1h 勝ち
       │          │          │          │
       ▼          ▼          ▼          ▼
   Phase 2-A   Phase 2-B   Phase 2-C   Phase 3
   (1h/4h)    (BTC/ETH)    (S on 1h)   (alt)
       │          │          │          │
       └──────────┴──────────┴──────────┘
                  │
                  ▼ Done 基準 pass の構成あり?
              ┌───┴───┐
              │       │
              YES     NO
              │       │
              ▼       ▼
           Phase 4   Phase 3 (まだなら) または bot 停止判断
```

## 4. リソース見積もり

| Phase | 期間 | 人時 | 主要コスト |
| --- | --- | --- | --- |
| 0 | 即時 / 継続 | 4h | LIVE config 変更 + dump CLI 整備（既に skeleton あり）|
| 1 | 1 週 | 16h | OHLCV データ取得 + resampling + 4 軸の rolling smoke |
| 2 | 2-4 週 | 40-80h | 勝者軸の full sweep + Phase 2 Done 基準評価 |
| 3 | 4-6 週 | 80-160h | 別系統（特に Grid）の engine 新規実装 |
| 4 | 4 週 | 16h | PAPER → LIVE 0.5x 監視 |

**Best case** (Phase 2 で勝者): 累計 8 週 / 80h
**Worst case** (Phase 3 まで): 累計 12 週 / 200h
**全滅 case**: Phase 1+3 = 7 週 / 100h で bot 自体の存続判断材料が揃う

## 5. 既存資産の活用度

S1〜S4 の作業はそのまま再利用可能:

| 資産 | Phase 1 | Phase 2 | Phase 3 |
| --- | --- | --- | --- |
| v2 component bundle (regime/exit/stop/sizing/entry の ABC) | ◎ | ◎ | ○ (entry signal 部分は流用、engine 周辺は要拡張) |
| 探索スクリプト 4 個 | ○ (引数追加だけ) | ○ | △ |
| バグ修正済み engine (walk_forward gap耐性、stochastic profile必須、partial close会計) | ◎ | ◎ | △ |
| backtest_engine の per-bar ExitPolicy | ◎ | ◎ | △ (新 system 用に再設計必要) |
| ADX/Donchian/ATR の cache | ◎ | ◎ | ◎ |
| dump_live_trades + build_execution_profile | ◎ | ◎ | ◎ |

新規実装が大きいのは **Phase 3** だけ。Phase 1/2 は既存資産で 80% カバー。

## 6. リスクと撤退条件

### 6.1 計画全体の撤退条件

- Phase 1 + Phase 2 で勝者ゼロ、かつ Phase 3 のいずれも Done 基準を満たせない（最悪 12 週）
- → **bot 開発を一時凍結**して、人的 / 資金リソースを別プロジェクトに振り向ける判断

### 6.2 Phase 内の早期撤退

- Phase 1 で **全 5 トラックが v0 baseline 未満**（mean +2.23 を超えない）→ trend-following 系統そのものを Phase 3 直行で諦める
- Phase 2 で 4 週使っても Done 基準 1〜2 個しか満たさない → 別軸 / Phase 3
- Phase 3 で 6 週使っても改善 0 → 全体撤退

### 6.3 サブ的リスク

- **LIVE profile 用 trade が集まらない**: Phase 0 の縮退運用で trade 頻度が落ちる → 36 trade のままで stochastic 評価精度が低い。**workaround**: 1 月後に 50 trade 程度しか集まらない場合、PAPER trade も混ぜて profile を作る
- **別 pair / 別 timeframe の data 取得が GMO API レート制限に引っかかる**: Phase 1 着手前に `fetch_ohlcv.py` のスループット見積もり
- **shadow_compare の実装が間に合わない**: Phase 4 の前提なので Phase 2 と並行で着手

## 7. 次の 1 週間のアクション（Phase 1 着手の具体タスク）

優先順:

1. **resample スクリプト作成** (~2h):
   - `research/scripts/resample_ohlcv.py` 新規。15m → 1h / 4h / 1d を OHLCV ルールで集約
   - 既存 `soljpy_15m_to_2026_05.csv` を入力に `soljpy_1h.csv`, `soljpy_4h.csv` を生成
2. **v0 を 1h / 4h で rolling 評価** (~3h):
   - `explore_track_a_rolling.py` をテンプレに `--bars-path` 引数を追加 → 1h / 4h で実行
   - Done 基準 (pos_rate / min / mean) と比較
3. **v2 best (D1_Volume_1.2x / B5+A4) を 1h / 4h で評価** (~2h)
4. **Supertrend / Donchian を 1h で評価** (~3h)
5. **BTC/JPY / ETH/JPY OHLCV 取得** (~4h):
   - `research/scripts/fetch_ohlcv.py` 確認、BTC/JPY と ETH/JPY 1y 分を取得
6. **BTC/JPY 15m で v0 評価** (~2h)
7. **Phase 1 結果まとめ → Phase 2 軸決定** (~2h)

**累計 ~18h 程度で Phase 1 完了**。1 週間で意思決定材料が揃う。

## 8. 本計画作成時点 (2026-05-21) のスナップショット

- 全テスト: 314 / 314 pass
- LIVE 状態: 既存設定継続中（縮退前）
- 探索資産: components/ 配下に 9 ABC + 14 具体実装
- データ: SOL/JPY 15m 1y を含む CSV 6 個
- ドキュメント:
  - [docs/gmo_bot_logic_exploration_plan.md](gmo_bot_logic_exploration_plan.md)
  - [docs/gmo_bot_logic_exploration_s4_findings.md](gmo_bot_logic_exploration_s4_findings.md) — S2〜S4 総括
  - [docs/gmo_bot_post_kill_exploration_plan.md](gmo_bot_post_kill_exploration_plan.md) ← 本ファイル

  整理メモ (2026-05-22): S2/S3 個別メモは S4 総括に結論を集約済みのため削除。

## 9. 判断のためのフレーミング

本計画を実施しなくても良い選択肢:

- **A. 縮退運用のみ続ける**: LIVE 0.5x で続け、新規探索は止める。緩やかに損失を出しながらマーケットが変わるのを待つ。コスト 0、期待値もほぼ 0
- **B. bot を一時停止 / 撤退**: LIVE を止めて手仕舞い。コスト 0、損失確定
- **C. 本計画を Phase 1 だけ実施 (1 週間 / 18h)**: 「どこに勝てる軸があるか」だけ確認して、Phase 2 以降は別途判断。最低コストで最大情報量
- **D. 本計画を full 実施 (8〜12 週)**: 勝者軸が見つかれば LIVE 復活、見つからなくても bot 撤退の根拠が揃う

**推奨は C か D**。Phase 1 だけなら投資回収可能性が高い（既存資産で 80% 流用、新規実装 ~18h）。Phase 2 以降は Phase 1 結果次第で判断。
