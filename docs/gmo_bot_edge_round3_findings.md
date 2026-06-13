# gmo_bot エッジ探索 Round 3 結果サマリ

> 計画は [gmo_bot_edge_round3_exploration_plan.md](gmo_bot_edge_round3_exploration_plan.md)。
> 本ファイルは各 Track の検証結果（accept/reject）を集約する。

## Track A2: XS reversal を GMO 実コストで monetize — REJECT（2026-06-06）

**仮説**: XS reversal の net REJECT は taker ~7bps 前提。板で maker 寄せして実効コストを break-even(~1.5bps) 近くへ下げれば、唯一の本物 edge（gross Sharpe+7）が net 黒転する。

**結論**: 棄却。**+7 gross は GMO 低流動板の close バウンス phantom であり、maker 化で剥がせる「コスト」ではない**ことを両 venue フロンティアで確定。

### 実 spread 実測（板 anchor）

収集済み板 [gmo_soljpy_ob_2026-06-05.csv](../research/data/raw/orderbook/gmo_soljpy_ob_2026-06-05.csv)（65 snap / 約72分）:
- spread_bps: median **8.5** / mean 9.2 / p10 1.0 / p90 20.8 → **half-spread ≈ 4.3bps**
- depth10 bid/ask vol ≈ 320/336 SOL、imbalance sd 0.19、OFI sd 0.51

### net フロンティア（xs_rev_L4_H4, cost_bps one-way, [explore_a2_execution_cost.py](../research/scripts/explore_a2_execution_cost.py)）

出力: [a2_exec_cost/frontier.json](../research/data/runs/a2_exec_cost/frontier.json)

| cost_bps(one-way) | 0 | 0.5 | 1.0 | **1.5** | 2.0 | 3.0 | **4.3**(half) | **7.0**(taker) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **GMO_JPY** ann Sharpe | **+7.07** | +4.39 | +1.72 | -0.96 | -3.63 | -8.95 | **-15.81** | -29.74 |
| GMO total_ret% | +2858 | +687 | +109 | -44 | -85 | -99 | -100 | -100 |
| **BINANCE_USDT** ann Sharpe | **+1.31** | -0.37 | -2.06 | -3.74 | -5.43 | -8.79 | -13.14 | -22.07 |
| Binance total_ret% | +1377 | -87 | -100 | -100 | -100 | -100 | -100 | -100 |

- **break-even one-way: GMO 1.32bps / Binance 0.39bps**。GMO half-spread 4.3bps は break-even の **3.3倍** → taker は net -100%。
- **phantom の確定**: gross は GMO +7.07 → Binance +1.31。差 +5.8 は **GMO close 系列に乗る bid-ask バウンスそのもの**（close≈mid の流動 venue で消える）。Round2 の「+7 は GMO 板固有、Binance +0.76〜1.31」を clean なフロンティアで再現・定量化。

### なぜ maker 化で取れないか（A2 仮説の決定的反証）

「+7 を取りつつ spread を払わない」は両立不能:
- **+7 を実現するには bouncing close で約定する必要がある = taker** で half-spread 4.3bps を払い net -100%。
- **spread を避けて maker で resting** すると、見えるのは bounce を含まない spread-free edge（= Binance の +1.31, break-even **0.39bps**）。これは現実的 maker 実効コスト（Binance taker ~5bps / maker でも約定優先のため adverse selection で実質正コスト）を**どの執行改善でも割れない**。
- GMO の wide-spread バウンスを maker で収穫する = **spread の market-making**（在庫・adverse selection・キュー位置リスク）であって、directional な XS/v2 bot のスコープ外の別戦略。close-to-close backtest は maker の adverse selection を一切モデルできないため、ここで net 黒転を主張するのは過大評価になる。

→ **計画の撤退条件「達成可能実効コストが ~3bps を割れない」に該当（実際は break-even 1.32bps すら執行改善で割れない）。A2 = REJECT。** 凍結結論「robust かつ net-tradeable な edge はゼロ」を、recollection でなく fresh なフロンティアで再確認した。

### 副次メモ: v2 の maker 執行は別物（incremental ops、新エッジではない）

v2 の edge は direction×hour の real directional edge（保有数時間）で bounce 由来ではない。よって v2 は maker 執行で taker spread を実際に節約でき net 改善の余地がある。ただしこれは既存 LIVE の bounded な執行最適化であって「新エッジ」ではない。投資判断は別途（執行系の maker 約定率 PoC が前提）。

## Round 3 全体への含意（A1/B/C の扱い）

- **A1（板 microstructure alpha）への含意**: realizable な microstructure edge の上限は spread-free の Binance 系列（+1.31 / break-even 0.39bps）。完璧な OFI signal でも同じ sub-1bps コスト壁に当たる。**A2 が gate だった通り、板収集を alpha 目的で常時化する投資は正当化されない**（計画どおり A2 先行で無駄打ちを回避できた）。
- **B（on-chain）/ C（センチメント）**: alt-data の「説明はするが予測しない」+ データ長の壁を共有。filter 用途の安価な 1 screen のみ（期待値低）。
- **pre-registered kill**: A2 が net 黒転せず（確定）、B/C も null を越えなければ 3 軸とも凍結に戻す。
