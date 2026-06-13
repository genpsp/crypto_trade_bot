# gmo_bot Round 4 計画: GMO spread の market-making（流動性供給）

- 対象: `apps/gmo_bot`（GMO コイン / SOL/JPY）+ 新規 HF 板データ + MM 実行層
- 位置づけ: Round 1–3（[findings 群](README.md)）で **「価格の方向/統計 alpha を予測して taker で取る」枠は完全に枯渇**。Round 3 A2 が「+7 の大半は GMO 広 spread のバウンス＝taker が払い maker が受け取る金」と定量。本 Round は枠組みを **方向予測 → 構造的流動性プレミアムのハーベスト（maker 側）** に転換する
- 前提: directional/XS/funding/alt-data は全滅確定。LIVE は `v2_dir_session_vol_time120` を維持（MM とは独立に併存）
- 作成: 2026-06-06

## 0. なぜ MM か（Round 3 A2 の含意）

Round 3 [findings](gmo_bot_edge_round3_findings.md) の確定事実:

- GMO SOL/JPY のクォート spread は **median 8.5bps**（板実測）。Binance SOLUSDT は **~1bps**。この **~7.5bps の差は構造的流動性プレミアム**（低流動 retail JP venue ゆえ）
- XS reversal の gross +7.07 のうち +5.8 は GMO close のバウンス＝**spread そのもの**。taker はこれを払い、**maker は受け取る**
- 「+7 を取る＝taker でバウンス約定」と「spread を避ける＝maker」は両立不能。**maker になって spread を稼ぐ＝MM** が、A2 が消去法で残した唯一の monetize 経路

**MM の本質的賭け**: クォート spread（8.5bps）が **adverse selection コストを上回るか**。上回れば realized spread > 0 で edge。これが本 Round 唯一の go/no-go 問題。

## 1. Phase 0: 実行可能性（確認済み + 要検証）

### 確認済み（コードベース）

- **LIMIT 注文は実装済み**: [gmo_api_client.py:118](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L118) `create_order(execution_type, price, time_in_force)`。ただし **bot は現状 MARKET（taker）しか発注しておらず**（[gmo_margin_execution.py:55](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L55)）、resting limit は新規実行パス
- **private WS あり**: [private_ws_client.py:18](../apps/gmo_bot/adapters/execution/private_ws_client.py#L18) `wss://api.coin.z.com/ws/private/v1`（約定/注文更新の受信基盤）
- **板収集スクリプトあり**: [collect_gmo_orderbook.py](../research/scripts/collect_gmo_orderbook.py)（REST `/v1/orderbooks` + `/v1/trades` を 15s polling、spread/imbalance/OFI/depth10 を記録）

### 検証完了（2026-06-07, desk + live public API）— **Phase 0 = GO（2大不安が両方有利に解決）**

| 項目 | 結果 |
| --- | --- |
| **post-only** | ✅ **GMO は国内唯一 Post-Only 対応**（[2020 プレスリリース](https://coin.z.com/jp/news/2020/09/6551/)）。`timeInForce="SOK"` で taker 化する注文を自動キャンセルし **maker 約定のみ保証**。MM 最大リスク（cross で taker 化）が消える |
| **maker 手数料** | ✅ **現物 SOL は maker rebate -0.03%（＝約定ごとに +3bps 受取）** / taker +0.09%。**rebate だけで XS reversal break-even(1.32bps) を超える**。レバレッジ SOL_JPY は maker 0%/taker 0.03% |
| **public WS 板** | ✅ `wss://api.coin.z.com/ws/public/v1`（orderbooks/trades/ticker）。subscribe は 1/s 上限だが data 配信は別。live requote の基盤あり（Phase 3 で統合） |
| **レート制限** | ✅ private 20 req/s（Tier1）→ cancel+replace で ~10 requote/s 可能。中頻度 MM に十分 |

### 重要な分岐: 現物 SOL（rebate 商品）を MM 対象にする

GMO の SOL は2商品で経済性が別物（live `/public/v1/symbols`）:

| 商品 | maker | taker | min/step | tick | live spread(参考) | 売り |
| --- | --- | --- | --- | --- | --- | --- |
| **現物 `SOL`**（取引所現物） | **-0.03%（rebate）** | 0.09% | 0.01 / 0.01 SOL | 1 JPY(~1bps) | 5.9bps（厚い板 500levels） | **在庫保有が必要（net short 不可）** |
| レバレッジ `SOL_JPY` | 0% | 0.03% | 0.1 / 0.1 SOL | 1 JPY | 2.0bps（薄い板） | 両建て可だが rebate 無・日次ロールオーバ |

→ **MM 対象は現物 `SOL`**。rebate +3bps/fill は round-trip で +6bps の下駄＝adverse selection への厚い cushion。代償は (1) 現物ゆえ net short 不可＝**long 在庫ベース + リバランス運用**、(2) 板が厚い＝**キュー競争**（rebate が厚いのは競争が激しいから。fill はキュー前方が捌けた時＝価格が抜ける直前に偏る＝adverse selection）。
→ **収集対象を現物 SOL に修正**（[collect_gmo_orderbook.py](../research/scripts/collect_gmo_orderbook.py) に `--symbol` 追加、現物 SOL + SOL_JPY 並行収集中）。

### 残検証（Phase 0 完了に向け軽微）

- 現物 SOL / SOL_JPY が post-only 対象銘柄か（SOK 指定可否）の最終確認（最小ロット 0.01 SOL≈100円の LIMIT+SOK 1発で実証）
- 現物 MM の在庫リスク限度・リバランス方式（spot long base の設計）

## 2. Phase 1: adverse selection 実測（データゲート・本 Round の心臓部）

**目的**: クォート spread（8.5bps）が adverse selection に食われずに残るか＝**realized spread > 0** を HF 板データで実測する。

**データ**: [collect_gmo_orderbook.py](../research/scripts/collect_gmo_orderbook.py) を**常時稼働**で数日〜数週蓄積（現状 16分のみ）。trades 全件取得済なので fill/adverse 分析に足る粒度。live MM 化前に WS へ移行。

**手法（標準マイクロ構造分解）**:

1. **fill モデル**: bid に resting した maker 買い注文は「market sell が bid を叩いた」とき約定（trades の side=SELL かつ price ≤ bid）。ask 側は対称。第一近似はキュー位置無視（touch で約定）
2. **realized spread**: 約定後 Δt（数秒〜1分）の mid 反転を測る。`realized_spread = 2 × (mid_fill − mid_{fill+Δt}) × sign`。これが正＝spread を取れている、負＝informed flow に食われている
3. **adverse selection / price impact 分解**: `effective_spread = realized_spread + price_impact`。imbalance/OFI で条件付けし、どの板状態で adverse が小さいか（＝安全に quote できる状態）を特定
4. **ネガコン**: ランダム時刻の擬似約定で baseline を引き、実約定の realized spread が baseline を超えるか置換検定

**MM 1往復の net P&L ≈ realized_spread（spread 捕捉）＋ maker rebate（現物 +3bps×2 = +6bps/round-trip）− adverse selection**。post-only なので taker fallback コストは無い。**rebate +6bps が adverse selection への下駄**なので、realized_spread 単体が負でも `realized_spread + 6bps > 0` なら go。

**Done（go）**: 上記 net P&L が **複数日・複数セッションで頑健に正**、かつ約定機会が十分（日次 fill 数）。imbalance/OFI 条件付けで adverse を有意に下げられる。キュー競争下の現実的 fill 率を見込んでも正。

**撤退（no-go）**: adverse selection が spread + 6bps rebate を恒常的に食う（net ≤ 0）→ **GMO の広 spread + rebate は toxic flow への正当な対価であってフリーランチでない**。MM 不成立で凍結に戻す。

## 3. Phase 2: quote/inventory シミュレーション（Phase 1 go なら）

**目的**: 在庫リスクと skew を入れた現実的 MM の P&L 分布を、蓄積 HF データ上で backtest。

**要素**:
- resting 両側 quote、fill モデル（Phase 1）、**在庫上限**、imbalance/inventory による **quote skew**（在庫過多側を引っ込め反対側を出す＝adverse 回避＋在庫回帰）
- メトリクス: 日次 P&L / Sharpe / 最大在庫 / drawdown / fill 数。手数料・ロールオーバ込み net

**Done**: net 日次 Sharpe が現行 LIVE 水準以上、最大在庫・DD が許容内、in-sample/holdout で頑健。

**撤退**: skew/在庫管理を入れても net 負 or 在庫リスクが過大 → MM 不成立。

## 4. Phase 3: live infra（Phase 2 go なら）

- public WS 板 + private WS 約定の統合、post-only 相当の発注/取消ループ、**在庫リスク限度 + kill-switch**、レート制限順守
- Gate A/B/C 相当: PAPER（or 最小ロット live）→ `position_size_multiplier=0.5` → 本サイズ。**MM は taker bot と執行系が別**なので shadow でなく実 fill ログで検証

## 5. 再利用資産と制約

| 再利用 | 箇所 |
| --- | --- |
| LIMIT 発注 API | [gmo_api_client.py](../apps/gmo_bot/adapters/execution/gmo_api_client.py) `create_order` |
| private WS 受信 | [private_ws_client.py](../apps/gmo_bot/adapters/execution/private_ws_client.py) |
| 板収集 | [collect_gmo_orderbook.py](../research/scripts/collect_gmo_orderbook.py) |
| 統計（置換/bootstrap/DSR） | [statistics.py](../research/src/eval/statistics.py) |

| 制約 | 影響 |
| --- | --- |
| HF 板履歴ゼロ（現状16分） | Phase 1 は**継続収集が前提**。本 Round 最大のリードタイム |
| 15s REST polling | research には可、live MM には WS 必須（Phase 3 infra） |
| post-only 不確実 | Phase 0 で潰す。最悪 taker 化リスク＝MM の前提崩壊 |
| 単一資産・taker 前提の既存 engine | MM は engine 外の新シミュレータ + 新執行層。既存 v2 とは独立 |

## 6. pre-registered kill（凍結に戻る設計）

| Phase | 撤退条件 |
| --- | --- |
| 0 | post-only 不可 or maker 手数料が spread を相殺 → 即 no-go |
| 1 | realized spread ≤ 0（spread は adverse selection の対価）→ MM 不成立・凍結復帰 |
| 2 | 在庫/skew 込みで net 負 → MM 不成立 |
| 全体 | Phase 0/1 で no-go なら凍結に戻し収集停止、現行 LIVE 維持のみ |

## 推奨ロードマップ

1. **Phase 0 を即実施**（desk + 最小ロット LIMIT PoC）— post-only と手数料で MM の前提を確認。最速 kill
2. 並行で **板収集を常時稼働**（Phase 1 のデータゲート＝最大リードタイム。今すぐ開始）
3. 数日蓄積後 **Phase 1 adverse selection 実測** → go/no-go
4. go なら Phase 2 シミュレータ → Phase 3 WS live infra（Gate 経由）
