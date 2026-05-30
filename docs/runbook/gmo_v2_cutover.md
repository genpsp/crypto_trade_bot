# Runbook: v2 cutover for `gmo_ema_pullback_15m_both_v0` (2026-05-21)

> **目的**: 現行 v0 戦略から v2 (`v2_dir_session_vol_time120`) へ LIVE cutover を実施する手順、ロールバック手順、kill-switch 基準、事後評価フローを定義する。
>
> **背景**: [docs/gmo_bot_exploration_findings.md](../gmo_bot_exploration_findings.md) §1 で stochastic_v1 p05 +62.13% / Phase 2 mean 基準クリアを確認。LIVE 30日で実環境 edge を検証する。

## 0. TL;DR

```
1. archive v0 ✓  ← /research/models/.../config/archive/v1_pre_cutover_2026-05-21.json
2. dump existing LIVE trades into baseline JSON
3. git commit + tag: live-cutover-v2-dir-session-2026-05-21
4. Firestore 更新: models/gmo_ema_pullback_15m_both_v0/config/current ← v2 内容
5. bot は config listener で 30 秒以内に新設定を反映
6. 30 分後: 新 trade に variant_id="v2_dir_session_vol_time120", config_version=2 が入っているか確認
7. 7日後: compare_live_versions で初期比較。kill-switch 確認
8. 30日後: 本格比較→ 1.0x への昇格判断
```

## 1. Cutover 前チェックリスト

### 1.1 コード状態確認

- [ ] `git status` クリーン
- [ ] `pytest tests/ -q` → 314/314 pass
- [ ] backtest 再現確認:
  ```bash
  source .venv/bin/activate
  python -m research.scripts.explore_phase1_axis_sweep \
    --bars research/data/raw/soljpy_15m_to_2026_05.csv \
    --timeframe 15m --pair SOL/JPY --windows 13 --window-bars 3000 \
    --variants v0_baseline "v2_dir_session+vol+time120"
  # expected: v2 mean +5.57 / pos_rate 69.2%
  ```

### 1.2 baseline トレード保存

- [ ] LIVE 過去 60〜90 日の trade を JSON にダンプ:
  ```bash
  python -m apps.gmo_bot.scripts.dump_live_trades_for_profile \
    --model-id gmo_ema_pullback_15m_both_v0 \
    --mode LIVE \
    --from-date-jst 2026-03-01 \
    --to-date-jst 2026-05-21 \
    --output research/data/execution_profiles/raw_trades/baseline_v0_2026-03_2026-05.json
  ```
- [ ] 上記ファイルが git に commit されている、または S3 等にバックアップ
- [ ] trade 数が想定どおり (LIVE 36 + その後の蓄積)

### 1.3 v2 設定確認

[research/models/gmo_ema_pullback_15m_both_v0/config/current.json](../../research/models/gmo_ema_pullback_15m_both_v0/config/current.json) が次を含むか:

- [ ] `strategy.name = "ema_trend_pullback_15m_v2"`
- [ ] `strategy.components.regime_gate` が composite で directional_session + volume_confirmed を含む
- [ ] `strategy.components.exit_policy.type = "time_exit"` (max_holding_bars=120)
- [ ] `execution.leverage_multiplier = 0.5` (初期縮退)
- [ ] `meta.config_version = 2`
- [ ] `meta.variant_id = "v2_dir_session_vol_time120"`
- [ ] `meta.note` に cutover 日付・理由が記述されている

### 1.4 archive 確認

- [ ] `research/models/gmo_ema_pullback_15m_both_v0/config/archive/v1_pre_cutover_2026-05-21.json` が存在
- [ ] 内容が cutover 直前の v0 設定と一致

### 1.5 git tag 準備

cutover commit に tag を打つ:

```bash
git add -A
git commit -m "v2 cutover: dir_session+vol+time120 (config_version=2)

Phase 2 findings (docs/gmo_bot_exploration_findings.md §1):
- ideal_v1 mean +5.57% / pos_rate 69.2% / break-even WR margin +8.63pt
- stochastic_v1 50-seed p05 = +62.13% (100% seeds positive)
- holdout walk-forward total +12.73%

Initial leverage 0.5x for 14-30 days; see docs/runbook/gmo_v2_cutover.md.

🤖 Generated with Claude Code
"
git tag -a live-cutover-v2-dir-session-2026-05-21 -m "v2 LIVE cutover"
```

## 2. Cutover 実施

### 2.1 Firestore config 更新

Firestore コンソールまたは下記コマンド (gcloud / firestore CLI) で:

**Path**: `models/gmo_ema_pullback_15m_both_v0/config/current`

**Body**: `research/models/gmo_ema_pullback_15m_both_v0/config/current.json` の全内容で**置き換え**

```bash
# Example using gcloud firestore (要 ADC 設定)
gcloud firestore documents set \
  "models/gmo_ema_pullback_15m_both_v0/config/current" \
  --data-file=research/models/gmo_ema_pullback_15m_both_v0/config/current.json \
  --project=$GCP_PROJECT_ID
```

または手動で Firestore コンソールから JSON を貼り付け。

### 2.2 反映確認 (cutover +30 分)

- [ ] bot のログで `config_changed` イベントが拾われている
- [ ] 次の 15m bar 評価サイクルで新 strategy が動作 (regime_gate_blocked 系の no_signal_reasons が出る)
- [ ] Firestore で新規 trade レコードを開いて確認:
  - `config_version = 2`
  - `variant_id = "v2_dir_session_vol_time120"`
- [ ] 余剰証拠金が縮小 (leverage 0.5x なので 1/2 の notional)

最初の数時間で **0 trade** でも正常（filter による）。最初の 24〜48h で trade が発生しなければアラート発動。

## 3. Kill-switch 基準と監視

### 3.1 連続損失系

- **5-day rolling realized PnL JPY < -3%** (= 残高に対する 3%): 一時停止 → 検証
- **連続 5 STOP_LOSS**: 一時停止 → backtest で同期間の挙動を確認

### 3.2 同期 / 一致率系

- **shadow_compare 一致率 < 80%** (LIVE と backtest の trade decision が一致): バグ可能性 → 即停止

### 3.3 オペレーション系

- **GMO ERR-* 連発** (24h で 10+ rejected entry): API / マージン異常 → manual 介入
- **bot 死活 timeout**: bootstrap.py 既存の alert チャネルに従う

### 3.4 監視コマンド (7 日に 1 回)

```bash
# 直近 7 日の v2 trade 集計
python -m apps.gmo_bot.scripts.dump_live_trades_for_profile \
  --model-id gmo_ema_pullback_15m_both_v0 --mode LIVE \
  --from-date-jst $(date -v -7d +%Y-%m-%d) \
  --to-date-jst $(date +%Y-%m-%d) \
  --output /tmp/post_cutover_week_1.json

# v0 baseline と比較
python -m research.scripts.compare_live_versions \
  --trades-json /tmp/post_cutover_week_1.json \
    research/data/execution_profiles/raw_trades/baseline_v0_2026-03_2026-05.json \
  --variant-a "v0_baseline" \
  --variant-b "v2_dir_session_vol_time120" \
  --output /tmp/week_1_comparison.md
```

## 4. ロールバック手順

### 4.1 設定だけのロールバック (コードに問題なし)

旧 v0 設定を Firestore に書き戻すだけ:

```bash
gcloud firestore documents set \
  "models/gmo_ema_pullback_15m_both_v0/config/current" \
  --data-file=research/models/gmo_ema_pullback_15m_both_v0/config/archive/v1_pre_cutover_2026-05-21.json \
  --project=$GCP_PROJECT_ID
```

bot は 30 秒以内に v0 設定を再読込。**trade_id の連続性は保たれる** (model_id 不変)。新規 trade は `config_version=1`, `variant_id` 無しになる。

### 4.2 コード障害ロールバック

新 gate / 新エンジン処理にバグがある場合:

```bash
git revert live-cutover-v2-dir-session-2026-05-21..HEAD
# または特定 commit のみ
git revert <commit_sha>
# デプロイ後 + Firestore config も v1 に戻す (4.1)
```

### 4.3 オープンポジションの扱い

cutover 時点でオープンポジションがある場合:

- 既存ポジションは現在の stop / TP で**現エンジンが管理し続ける**（v2 components は新規エントリーにのみ作用）
- **手動クローズ不要**。自然 close (TP/SL hit) まで持続。

## 5. 評価フロー (週次)

### 5.1 Week 1 (cutover +1〜7 日)

- 監視のみ。trade 数 < 5 でも正常
- daily summary を Slack で確認
- 想定外のエラーが出ていないか

### 5.2 Week 2-4 (cutover +8〜30 日)

- 週次で `compare_live_versions.py` を走らせる
- 30 日終了時点で:
  - **Phase 2 Done 基準 (PAPER 代替) クリア → leverage 1.0x へ昇格判断**
  - mean PnL < 0 / WR < 35% → kill-switch 発動、Phase 2.5 deep dive へ

### 5.3 評価項目 (compare_live_versions.py 出力)

- n (trade 数): 30 日で 15-25 程度の想定
- WR: backtest 42% を ±5pt で着地するか
- mean_pnl_jpy: 余剰証拠金比で 0.5x ベースの期待値
- bootstrap CI: B-A の 90% CI が完全に正側なら強い証拠
- LONG/SHORT 内訳: backtest の 82%/18% に近いか

### 5.4 stochastic profile 更新

30 日 trade 蓄積後:

```bash
python -m apps.gmo_bot.scripts.dump_live_trades_for_profile \
  --model-id gmo_ema_pullback_15m_both_v0 --mode LIVE \
  --from-date-jst 2026-05-22 --to-date-jst 2026-06-21 \
  --output research/data/execution_profiles/raw_trades/post_v2_30d.json

python -m research.scripts.build_execution_profile \
  --broker GMO_COIN --pair SOL/JPY \
  --input research/data/execution_profiles/raw_trades/post_v2_30d.json \
  --output research/data/execution_profiles/gmo_soljpy_v2.json
```

新 profile で stochastic_v1 を再走させ、p05 の経時推移を確認。

## 6. 1.0x 昇格判断 (cutover +30 日)

### 6.1 必要条件

- [ ] **mean_pnl_jpy > 0** (LIVE 30 日)
- [ ] **WR ≥ 38%** (break-even 33.3% + 5pt margin)
- [ ] **連続 STOP_LOSS 最大 ≤ 4**
- [ ] **bootstrap CI の lower bound ≥ 0** (PnL diff vs baseline)
- [ ] **shadow_compare 一致率 ≥ 90%** (LIVE vs backtest)
- [ ] **stochastic profile 更新後の backtest p05 ≥ +30%** (劣化していないか)

### 6.2 昇格手順

```bash
# 1. config 更新: leverage_multiplier 0.5 → 1.0
# 2. config_version bump (2 → 3)
# 3. meta.note update
# 4. archive 現状 (research/models/.../config/archive/v2_05x_2026-06-21.json)
# 5. git commit + tag (live-promote-v2-1x-2026-06-21)
# 6. Firestore update
```

新 trade は `config_version=3`, 同 `variant_id="v2_dir_session_vol_time120"`。1.0x 期間の追跡用。

## 7. 重要な未解決リスク

- **w12 相当の regime での挙動**: backtest で唯一の大幅損失 window (最新 31 日)。LIVE で再現する可能性
  - 対策: 5-day rolling kill-switch でカット
- **slippage の実測 vs template profile の差**: profile は LIVE 36 trade 由来
  - 対策: 30 日で新 profile rebuild
- **storm_short_v0 / loss_streak guard の互換性**: 新 v2 components と既存 guard の二重作用
  - 対策: shadow_compare で trade decision の一致を週次確認

## 8. 連絡先 / エスカレーション

- **bot owner**: (このフィールドは実運用者が埋める)
- **monitoring channel**: (Slack ch ID)
- **incident playbook**: bootstrap.py の `_setup_consecutive_failure_alert` を参照

## 9. 関連ドキュメント

- [探索結果サマリ (採用根拠 / Phase 1・2・3-V / post-mortem)](../gmo_bot_exploration_findings.md)
- [新エッジ探索計画 (LIVE 後の後続探索)](../gmo_bot_new_edge_exploration_plan.md)

---

**Cutover 実施記録欄** (実施時にここに追記):

- 実施日時 (JST):
- 実施者:
- 直前 LIVE balance JPY:
- cutover commit SHA:
- 反映確認後の最初の v2 trade_id:
- 備考:
