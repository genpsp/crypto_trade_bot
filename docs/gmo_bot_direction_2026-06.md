# gmo_bot 探索行き詰まりの総括と次方向（2026-06-10）

- 位置づけ: Round 1–4 + alt-data Round が全て REJECT / marginal で停止した時点での**戦略方向レビュー**。新 Round の計画書ではなく、「どの軸に残り EV があるか」の棚卸しと優先順位づけ。
- 前提 findings: [exploration](gmo_bot_exploration_findings.md) / [Round1](gmo_bot_new_edge_findings.md) / [Round2](gmo_bot_edge_round2_findings.md) / [alt-data](altdata_edge_findings.md) / [Round3](gmo_bot_edge_round3_findings.md) / [Round4 Phase0](gmo_bot_mm_round4_findings.md)

## 1. 現在地: 行き詰まりの正体は「3つの壁」

4 Round + alt-data を通じて、行き詰まりは探索不足ではなく**構造的な壁 3 枚**に収束している。

| 壁 | 確定した事実 | 出典 |
| --- | --- | --- |
| **① alpha の壁** | OHLCV 組み替え / 上位足(〜4h) / 別 pair / XS reversal / XS momentum / funding carry / funding timing / alt-data 全系統で、**robust（across regime）かつ net-tradeable な edge はゼロ**。見かけの edge は全て venue 固有バウンス・期間固有・偽相関で proper 検証（長期・他 venue・ネガコン）で消失 | Round1/2, alt-data |
| **② コストの壁** | GMO 実効 taker 片道 ~7bps vs break-even 1.32bps。唯一持続する gross（XS reversal）も流動 venue では +0.76〜1.31 で sub-1bps のコスト壁を越えられない。**intraday 統計 alpha はどの venue でも net 化不能** | Round2 A, Round3 A2 |
| **③ 執行・レイテンシの壁** | 構造的流動性プレミアム（spread 8.5bps + 現物 rebate +3bps/fill）は実在するが、収穫競争の決定変数は**キュー位置/レイテンシで retail VPS は構造的後列**。机上 EV は marginal〜negative、かつ**受動的板収集では go/no-go を判定できない**（p_toxic は自分の fill でしか測れない） | Round4 Phase0 |

加えて環境制約: GMO の流動ユニバースは高相関 5 メジャーのみ（XS に必要な breadth が構造欠如）、海外 perp venue は居住者向け提供停止で合法的に使えず、国内他 venue も同様の狭いユニバース・広 spread。**「venue を変えれば解ける」経路は存在しない**。

### v2 LIVE の現況（2026-06-03 時点）— 健全

唯一の採用 edge `v2_dir_session_vol_time120` は稼働中。live 実績 17 trade（05-22〜06-03, [raw_trades](../research/data/execution_profiles/raw_trades/v2_live_2026-05-22_2026-06-03.json)）: gross +909 JPY / WR 9/17、下落局面で SHORT TP が機能。kill-switch 運用継続。**行き詰まっているのは「次の edge」であって現行運用ではない**。

## 2. 候補方向の棚卸し

「まだ測っていない決定変数」or「edge 不要で EV 正」の軸だけが残っている。直交する 3 本 + フォールバック 1 本。

### 方向 A: MM 極小 live 実験（Round 4 の唯一の続行手）— 最優先

Round 4 Phase 0 が残した二択「(1) 極小 live post-only 実験 (2) MM 棚上げ」の (1) を実施する。

- **何を測るか**: 現物 SOL に 0.01 SOL（≈¥100）の post-only(`SOK`) 両側 quote を置き、**realized p_toxic（自分の fill の毒性）と realized spread + rebate − adverse の net** を直接計測。これは机上でもデータ購入でも測れない、本 Round 唯一の決定変数。
- **新規の改善点 — fair-value アンカー quoting**: Round 4 の机上モデルは「GMO mid に quote → 価格が動くと pick-off」を仮定していた。しかし SOL/JPY の price discovery は Binance 側にあり（GMO は follower 市場）、**quote を Binance SOLUSDT × implied USDJPY（= GMO BTC/JPY ÷ BTCUSDT で外部 FX feed 不要）にアンカーすれば、adverse selection の主経路「stale quote の踏み抜き」を retail レイテンシでも大幅に削れる**。p_toxic はキュー位置だけでなく quoting policy の関数であり、ここは机上 EV に織り込まれていない上振れ余地。実験は naive-mid と fair-value アンカーの A/B で設計する。
- **設計骨子**: 全 quote/fill と fill 前後 ±60s の mid 軌跡をログ → realized spread 分解。条件付け: 時間帯（spread p90 20.8bps のワイドセッション）、imbalance、片側 quote + 在庫 skew。目標 fill 数 ≥ 数百/arm。
- **コストと kill**: 開発 = 既存 LIMIT API + private WS の上に最小 quote ループ（数日）。資金リスク ≤ ¥10k 規模。**kill（pre-registered）: fair-value アンカー込みでも複数セッションで `realized + rebate6bps ≤ 0` → MM を恒久 kill し凍結復帰**。
- **なぜ最優先か**: 残存仮説の中で唯一「確定収入（rebate）×未測定の決定変数」の構造。情報量/円が最大で、no-go でも Round 4 を完全クローズできる。

### 方向 B: v2 の maker 執行化（edge 不要の確定改善）— A と同一インフラ

Round 3 findings 副次メモの具体化。v2 は実測片道 ~7bps（slippage 2.7 + fee 4.4 median）を taker で払っているが、edge は数時間ホールドの directional で bounce 由来ではないため、**執行改善がそのまま net に乗る**。

- **Step 1（ほぼノーリスク）: TP exit を resting post-only limit 化**。trigger 到達後に MARKET ではなく、entry 直後から TP 価格に SOK limit を置く。同じ価格で taker 3bps + slippage を節約し、早期 resting でキュー優先も取れる。live 17 trade 中 TP exit 9 件 → 平均 ~3-4bps/trade の確定改善。
- **Step 2（要 PoC）: entry の maker 化**。signal 後に touch へ SOK limit。**注意: directional 戦略の limit entry は「価格が戻った時だけ約定」= 約定が loser に偏る selection bias を持つ**ため、fill 率だけでなく filled vs missed の条件付き PnL を比較して判定する。backtest 側は cost sweep（7bps→2-4bps）で改善上限を先に定量化。
- **EV 感**: v2 の per-trade 経済性は ~20bps オーダーに対し、片道 5-7bps の節約は round-trip で net edge を大きく押し上げ得る。borderline 戦略（rolling min 負、holdout fail 歴）の安全マージン拡大として、新 edge 探索より確度が高い。
- A の quote ループと執行モジュールを共有 → **A/B は 1 つの post-only 執行投資の両面**。

### 方向 C: 日足 long-flat trend screen（最後の未踏・安価な 1 screen）

全 REJECT は intraday（15m/1h/4h）の組み替え・XS・carry・外部データであり、**「純粋 price の日足 time-series trend を複数年履歴で」は未実施**（Round1 上位足は 4h 止まり、alt-data は外部データの daily timing）。直近 commit で 1d 探索インフラが入っており、これを pre-registered な単発 screen として消化する。

- **なぜ日足だけ生き残り得るか**: コストの壁②は horizon に反比例する。日足 vol 300-500bps に対し RT 14bps はノイズ（15m では cost ≈ signal で全滅した）。TS momentum は数少ない premium 級（anomaly 級でない）候補で長期履歴で生存実績がある。
- **実装制約（重要）**: GMO レバレッジの rollover 0.04%/日 = 年 14.6% で multi-day ホールドは死ぬ。→ **long-flat を現物で**（maker rebate 側）。short 側は捨てる（long-flat が classic に主成分でもある）。
- **規律**: 全滅 4 Round の後なので prior は低い。configs ≤ ~20（lookback 20-90d × 2-3 ルール × BTC/ETH/SOL）、Binance 日足 6-8 年 + GMO 円建てで sub-period 3 分割、DSR + 循環シフトネガコン、**single shot**。工数 ≤ 2 日。**kill: DSR p<0.10 across sub-periods を満たす config ゼロ → 日足軸もクローズ**。

### 方向 D（フォールバック）: 凍結維持 = v2 運用のみ

A/B/C が全て no-go の場合の正規の終着点。Round 2 撤退条件の通り「v2 LIVE の運用・リスク管理のみ、新規探索停止」。**全滅確率は体感で五分程度あり、その場合に -EV な探索へ資源を投じず止まれたなら、それは研究プログラムの成功であって失敗ではない**（4 Round の検証規律が守った損失は実額で大きい）。

## 3. 推奨ロードマップ

```
[共有] post-only 執行モジュール（LIMIT+SOK + private WS fill 監視）
   ├─ B-Step1: v2 TP の resting limit 化（確定改善・即 live 可）
   ├─ A: MM 極小 live 実験（fair-value アンカー A/B、数週間）
   │     ├─ go  → Round4 Phase2（quote/inventory sim）→ Phase3
   │     └─ no-go → MM 恒久 kill
   └─ B-Step2: v2 entry maker 化 PoC（A のログで selection bias も検証）
[独立] C: 日足 long-flat screen（≤2日・single shot）
   ├─ pass → 新 Round 計画（Gate A 相当から）
   └─ fail → 日足軸クローズ
全 no-go → D: 凍結維持（v2 運用のみ）
```

| 優先 | 施策 | 工数/期間 | リスク資金 | 判定変数 |
| --- | --- | --- | --- | --- |
| 1 | B-Step1: TP resting limit | 〜1日 | ほぼゼロ | （確定改善・判定不要） |
| 2 | A: MM 極小実験 | 数日dev + 2-4週run | ≤¥10k | realized net/fill > 0 |
| 3 | C: 日足 screen | ≤2日 | ゼロ（research） | DSR p<0.10 全 sub-period |
| 4 | B-Step2: entry maker 化 | A と相乗り | 小 | filled/missed 条件付き PnL |

## 4. 明示的に閉じる方向（再訪防止）

| 方向 | 閉じる根拠 |
| --- | --- |
| intraday OHLCV 組み替え（15m-4h 全系統） | S2-S4 / Phase1 / Phase3-V で枯渇確定 |
| XS reversal / XS momentum（全 venue） | gross は GMO バウンス phantom or sub-1bps 壁。breadth も構造欠如 |
| funding carry / funding timing | 2.6 年 OOS で消失（2025-26 限定の period-specific） |
| alt-data 全系統 | ネガコン較正で全て偽陽性と確定 |
| alpha 目的の HF 板収集（A1） | realizable 上限が Binance sub-1bps 壁（Round3） |
| 受動的板収集による MM 判定 | p_toxic は自分の fill でしか測れない（Round4 Phase0） |
| 海外 venue 移行 | 居住者向け提供停止で非合法/不能。かつ ③/C は他 venue でも net 死亡済み |
| GMO レバレッジの multi-day ホールド | rollover 0.04%/日 = 年 14.6% |

## 5. 一行結論

**「予測する」軸は規律ある検証で完全枯渇が確定した。残り EV は (1) 確定収入×未測定変数の MM 極小 live 実験、(2) edge 不要の v2 執行改善、(3) コスト壁の外にある日足の単発 screen の 3 本だけで、いずれも安価・kill 条件つき。全滅なら凍結維持が正解であり、それは失敗ではない。**
