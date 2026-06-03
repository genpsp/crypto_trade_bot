# gmo_bot エッジ探索 Round 2 結果サマリ

> 探索計画は [gmo_bot_edge_round2_exploration_plan.md](gmo_bot_edge_round2_exploration_plan.md)。
> 本ファイルは各 Track の検証結果（accept/reject）を集約する。

## Track A: XS reversal を GMO 実コストで再評価 — REJECT（taker 確定死／maker は prior 低、要 PoC）（2026-06-02）

**仮説**: ③ の net REJECT は taker 7bps 前提。GMO レバレッジ（per-trade 手数料ゼロと想定）＋maker fill の実効コストなら break-even（片道 ~1.5bps）を越えられる。

**結論**: 仮説の前提（per-trade 手数料ゼロ）は誤り。taker 経路は実測で確定死。maker 経路のみ理論上生存余地があるが、統計的頑健性に必要なコスト予算が ~0.5bps しかなく、reversal の passive limit では adverse selection でほぼ確実に超過する。backtest では解けず maker 実行 PoC が必要だが prior は低い。

### step 1: GMO 実効コストの実測

LIVE v0 baseline の実約定 74 件（[execution_profiles/raw_trades/baseline_v0_2026-03_2026-05.json](../research/data/execution_profiles/raw_trades/baseline_v0_2026-03_2026-05.json)）で trigger 価格 vs 実 fill + 手数料を実測:

| 項目 | 片道 median | 片道 mean |
| --- | ---: | ---: |
| スリッページ（adverse） | +2.7bps | +8.8bps（不利テール厚） |
| 手数料 | +4.4bps | +8.5bps（小口で歪み） |
| **実効片道コスト** | **~7bps** | ~17bps |

- GMO 取引所レバレッジ SOL/JPY の公式 fee: **maker 無料 / taker 0.03%(3bps) / レバレッジ手数料 0.04%/日**（[fee 表](https://coin.z.com/jp/corp/guide/fees/)）。実測 median 手数料 4.4bps は taker 0.03%＋端数と整合 = LIVE は taker fill。
- **「GMO レバレッジは per-trade 手数料ゼロ」という Round 2 計画の前提は誤り**（taker は 3bps、加えて spread crossing）。
- ロールオーバ: xs_rev_L4_H4 は H=4bar=1h 保有 → 0.04%×(1/24)≈**0.17bps と無視可能**（短保有前提は正しかった）。

### step 2: maker 前提の net フロンティア（既存3銘柄, Track③ loop）

実効片道コストを sweep した xs_rev_L4_H4（出力は再現可能、[explore_track3_cross_sectional.py](../research/scripts/explore_track3_cross_sectional.py) を `--cost-bps` で）:

| 実効片道コスト | ann Sharpe | DSR p | roll_pos% | total% |
| ---: | ---: | ---: | ---: | ---: |
| 0 bps | +7.07 | 0.000 | 100% | +2858 |
| 0.5 bps | +4.39 | 0.003 | 100% | +687 |
| 1.0 bps | +1.72 | 0.481 | 62% | +109 |
| 1.5 bps | -0.96 | 0.997 | 38% | -44 |
| 2.0 bps | -3.63 | 1.000 | 15% | -85 |
| sol_buyhold（baseline） | -0.16 | 0.566 | 62% | -36 |

- **統計的に頑健（DSR p<0.10・全窓 positive）なのは実効コスト ≤ ~0.5bps のみ**。1bps で既に DSR p=0.48（非有意）・roll_pos 62%。break-even（Sharpe>0）は 1.0〜1.5bps の間。
- taker 実測 ~7bps は完全に死亡圏。
- maker は fee=0bps なので予算 ~0.5bps は**全て adverse selection 用**。15m reversal は本質的に passive limit が「動きが逆行した時だけ約定する」= adverse selection が数 bps オーダーになりやすく、0.5bps 未満は現実的に困難。

### 含意・撤退

- **taker 経路: 確定 REJECT**（実測 ~7bps >> 0.5bps）。
- **maker 経路: prior 低だが backtest では解けない**。可否は (a) post-only 約定率、(b) 約定の adverse selection を **live maker PoC で実測**しないと判定不能。0.5bps 予算は厳しく、execution infra（マルチ資産同時 post-only・部分約定ハンドリング）の投資に見合う期待値は低い。
- **判定**: Track A は実質 REJECT。唯一の復活経路は「near-zero adverse selection の maker 執行 or 低コスト外部 venue」で、現行 GMO taker LIVE への寄与は無し（Round 1 ③ の結論を実コストで確定）。
- 次は Track B（低頻度 XS momentum＋ユニバース拡張）へ。低頻度＝回転が 1/10 以下になり、taker ~7bps でもコスト耐性が出る可能性がある側。

## Track B: 低頻度 XS momentum＋銘柄拡張 — REJECT（流動的コアに edge 無し／edge は illiquid 銘柄に集中＝取引不能）（2026-06-02）

**仮説**: 15m intraday は reversal 優位・momentum 弱（microstructure）。日次以上の cross-sectional momentum は別系統で回転低=7bps でもコスト耐性。3 銘柄は breadth 不足なのでユニバース拡張で本検証。

**結論**: 棄却。取引可能（流動的）な 5 メジャーには gross ですら edge 無し。広いユニバースで見える momentum edge は illiquid alts（ADA/BCH）に集中し、そこは実コストが 7bps を大きく超える（Track A の壁）＝取引不能。GMO レバレッジの流動ユニバースは高相関 5 メジャーのみで XS に必要な breadth が構造的に欠如。

### step 1: ユニバース拡張

GMO レバレッジ JPY は 12 銘柄（[public/v1/symbols](https://api.coin.z.com/public/v1/symbols)）。SUI は履歴浅で除外、新規 8 銘柄を [fetch_gmo_universe_15m.py](../research/scripts/fetch_gmo_universe_15m.py) で取得。**bar 数=流動性**が明確に分かれた:

| 流動性 | 銘柄（1y 想定 ~43.6k bar 比） | 判定 |
| --- | --- | --- |
| 厚い | XRP(1.00) DOGE(0.99) SOL(0.94) BTC(0.90) ETH(0.90) | 取引可能コア |
| 中 | BCH(0.74) ADA(0.66) | 流動性懸念 |
| 薄い(gappy) | LTC(0.48) DOT(0.43) LINK(0.42) ATOM(0.23) | 低流動性=除外 |

### step 2: 低頻度 XS 評価（[explore_track_b_xs_lowfreq.py](../research/scripts/explore_track_b_xs_lowfreq.py)、日次以上 rebalance, top-k/bottom-k）

| ユニバース | 構成 | 最良 momentum config | net 7bps | gross 0bps | DSR p |
| --- | --- | --- | ---: | ---: | ---: |
| 7 銘柄（+ADA/BCH, 20k bar） | k=3 | xs_mom_L7d_H3d | **+1.65**（baseline 超え） | +1.91 | 0.565〜0.665（非有意） |
| **5 銘柄 liquid（37k bar）** | k=2 | xs_mom_L14d_H3d / L3d_H3d | **+0.36** | **+0.71** | **0.86〜0.93（edge 無し）** |
| baseline | — | sol_buyhold / ew_basket | -0.17 / -0.04 | 同 | 0.52〜0.57 |

- 多日 momentum は 15m reversal と逆に **direction は正しい**（長 horizon で long winners/short losers が機能する兆候）が、流動的 5 銘柄では gross ですら DSR p≈0.86〜0.93 で有意 edge 無し。
- 7 銘柄の +1.65/+1.91 は ADA/BCH を basket に入れた時のみ出る = **edge は illiquid alts 由来**。そこは実 spread が広く（Track A: 流動的 SOL ですら実効 ~7bps）7bps 想定は非現実的＝取引不能。
- Done 基準（net rolling Sharpe>SOL buyhold かつ DSR p<0.10）: 取引可能ユニバースで **DSR p<0.10 を一度も満たさず** → REJECT。

### 含意

- **GMO レバレッジ venue は XS 戦略に構造的に不適**: 流動ユニバース = BTC/ETH/XRP/DOGE/SOL の高相関 5 メジャーのみで、XS に必要な breadth（10-30 銘柄）も低相関も欠く。breadth を足す唯一の手段（alts）は流動性が無く取引不能。
- Track A（reversal）と Track B（momentum）で同じ壁を別系統から確認: **GMO の取引可能ユニバースに cross-sectional edge は無い**。XS を取るなら多数の流動 alt perp を持つ低コスト venue が前提（Round 1 ③ の結論と一致）。

## Track D: funding tail-filter を v2 LIVE へ — step 1 完了（安全な marginal+・ただし Gate A 非決定・LIVE 投入は要判断）（2026-06-02）

**step 1 の目的**: Round1 ⑤ の +0.23pt / pos_rate 69→85% を独立ハーネス（Gate A holdout + rolling）で再現確認し、LIVE 投入（step 2-4）の是非を判断する。

**実装**: sweep case で composite gates に funding gate（high +0.0001 / low -0.0002）を追加 = [gmo_15m_funding_filter_track_d.yaml](../research/sweeps/gmo_15m_funding_filter_track_d.yaml)。base は LIVE v2 `current.json`、ideal_v1、holdout split = train〜2026-03-11 / test 2026-03-12〜05-20。

### 結果（v2_baseline vs v2+funding）

| カット | baseline | +funding | 差 |
| --- | --- | --- | --- |
| rolling 90d×13窓 mean | +19.08（pos100% min+1.27） | +19.59（pos100% min+1.60） | **+0.51pt / min +0.33** |
| rolling 31d×43窓 mean | +4.54（pos77% min-8.77） | +4.70（pos79% min-8.77） | **+0.16pt / pos +2pp / min同** |
| holdout(2026-03-12〜05-20) pnl | -10.27（ci_low-23.0 / DSR0.997 / r2dd-0.89） | -9.03（ci_low-21.6 / DSR0.996 / r2dd-0.78） | +1.2pt（**両者とも Gate A fail**） |
| trades（rolling 31d計） | 1229 | 1204 | -25 |

### 結論

- **funding filter は全カットで一貫して weak positive・downside 無し**（mean +0.16〜+0.51pt、min 同等以上、~25 trade 除去、悪化窓ゼロ）= 安全な robustness tweak。符号は Round1 ⑤ と整合。
- ただし **magnitude は小さく partition 依存**。Round1 ⑤ headline の pos_rate 69→85%（+16pp）は 13 窓の小N増幅で、43 窓では +2pp（77→79）に収束。実効は「数 trade の tail を削る」程度。
- **Gate A は非決定**: holdout（直近の荒れレジーム）では baseline v2 自体が fail（ci_low-23 / DSR≈1）で、funding は +1.2pt 改善するが救済しない。**v2 の LIVE リスク本体（直近レジーム decay = kill-switch 対象）は funding では解決しない**。
- **判定（step 1）**: research としては ACCEPT（安全・marginal+）。だが効果は +0.2〜0.5pt mean / 安全性のみで、撤退条件 D の閾値（+0.23pt）を straddle。**LIVE 投入（外部 Binance funding fetch・fail-open orchestration・lookahead 安全性・執行系冗長化）のコストに見合うかは要判断**。リスク本体に効かない polish に外部 API 依存を増やす是非がトレードオフ。
- **判断（2026-06-02）: step 2-4 は見送り**。funding が LIVE リスク本体（直近レジーム decay）に効かず、外部 API 依存を増やす割に polish が小さいため。FundingGate / sweep YAML は資産として保持し、低コスト執行や別用途で再訪余地。次は Track B（ユニバース拡張）へ。

## Track C: funding carry basket — ✅ PoC 成功（real & 低コスト venue で取引可能な edge／GMO 不可）（2026-06-02）

**仮説**: funding 単体（Round1 ⑤ SOL）は弱いが、多 perp の long 低funding / short 高funding バスケット（低回転）は別系統。

**結論**: 成功。gross で DSR p=0.007 の頑健な edge。日次 rebalance で turnover を半減させると **Binance maker(~1-2bps) で Done 基準（net ann Sharpe>各単体 & DSR p<0.10）を達成**。**探索全体（Round1+2）で最も monetize 可能な edge**。ただし perp = Binance で GMO native 不可＝venue 移行が前提。

### データ

Binance USDⓈ-M `fapi/v1/fundingRate`（認証不要・markPrice 込み）で流動的 20 perp（2025-03〜2026-06 各 1375 点）を取得 = [fetch_binance_funding_universe.py](../research/scripts/fetch_binance_funding_universe.py) → `research/data/raw/funding/`。PnL/period = Σ_a[ w_a·priceReturn_a − w_a·funding_a ] − turnover·cost。

### 結果（[explore_track_c_funding_carry.py](../research/scripts/explore_track_c_funding_carry.py), 20 perp, n_trials=13）

| config | cost | ann Sharpe | DSR p | roll_pos% | total% |
| --- | ---: | ---: | ---: | ---: | ---: |
| carry_k5（8h rebalance, gross） | 0bps | +2.64 | **0.007** | 92% | +192 |
| carry_k3（8h rebalance, gross） | 0bps | +2.41 | **0.016** | 75% | +253 |
| **carry_k3_R3（日次 rebalance）** | **1bps** | **+2.47** | **0.075** ✓ | 75% | +261 |
| carry_k3_R3 | 2bps | +2.26 | 0.116 | 75% | +221 |
| carry_k3_R3 | 3bps | +2.06 | 0.172 | 75% | +185 |
| carry_k3_R3 | 5bps | +1.64 | 0.323 | 67% | +125 |
| carry_k3（8h rebalance） | 7bps | -1.18 | 0.997 | 25% | -56 |
| ew_long / btc_buyhold（baseline） | 7bps | -0.31 / -0.18 | — | 42/50% | -41/-19 |

- **gross は DSR p≈0.007〜0.016 で統計的に頑健**（探索全体で Track③ reversal に次ぐ2例目、かつ carry は別系統）。
- **turnover が壁だが性質が違う**: naive 8h rebalance は turnover 1.92/period でほぼ総入替 → 7bps で全滅。だが carry の PnL は「保有による funding 受取」由来なので、**日次 rebalance（R3）で turnover 0.865 に半減しても carry は保たれる**（R9/R21 は保有が古び carry 劣化で逆効果＝freshness と turnover の sweet spot が日次）。
- break-even は片道 ~5-6bps（Sharpe>0）、**DSR 頑健は ≤1bps**。Binance perp maker ~2bps（BNB/VIP で ~1bps）+ 日次 passive 執行で射程内。Track③ reversal（要 ~0.5bps）より遥かに緩い天井。
- **Done 基準達成**（≤1bps で ann +2.47 / DSR p<0.10 / 全 baseline 超え）。

### 含意・留保

- **Round2 で唯一、real かつ低コスト venue で取引可能な edge**。A/B が「GMO 流動ユニバースに XS edge 無し」を示したのに対し、C は「Binance perp に funding carry edge が実在し maker で取れる」を示した＝venue 移行の具体的根拠。
- 留保: (a) long-short は funding ランクで組むため β/factor 中立でない（roll_min ~-7、market exposure 残る → β ヘッジで改善余地）、(b) 20 perp は 2025-03 生存銘柄で軽い survivorship、(c) Binance の funding/手数料の将来変化リスク、(d) GMO LIVE 経路ではない＝新規 venue の執行・カストディ・税務の検討が前提。
- 次段は research では Gate A 相当（holdout/β中立版/maker 約定モデル）、その先は Binance 執行 PoC。

### ⚠ 候補3: 長期窓での再検証 — C は out-of-sample で再現せず（2026-06-02）

C の「PoC 成功」を proper に固めるため、funding 履歴を 2021〜で再取得（[funding_long](../research/data/raw/funding_long/), 20 perp）。**markPrice は 2023-10-31 以降のみ**存在するため価格込みの carry 評価窓は 2.6 年（元の 14ヶ月の約2倍）に拡張できた。

| 評価窓 | carry_k5 gross Sharpe | DSR p |
| --- | ---: | ---: |
| 元の 14ヶ月（2025-03〜, Round2 C 採用窓） | +2.64 | **0.007** |
| **拡張 2.6年（2023-10〜2026-06）** | **+1.08** | **0.373（非有意）** |

**サブ期間分解（決定打, carry_k5 gross）**:

| 期間 | gross Sharpe |
| --- | ---: |
| 前半 2023-10〜2025-02（n=1460） | **-0.26** |
| 後半 2025-03〜2026-06（n=1374） | **+2.64** |

- **edge は 2025-2026 に完全集中**。2023-2024 はむしろ負。Round2 C の Done 達成は favorable サブ期間限定のアーティファクトだった。
- **β分解**: carry の beta は -0.08（既にほぼ market-neutral、隠れβ起因ではない）。だが β中立後でも 2bps maker で alpha Sharpe は +1.14（gross）→ **+0.09（≒ゼロ）** に崩壊。net では取れない。
- **判定改訂: C は robust な edge ではない**。funding 分散が大きい regime（2025-26）でのみ harvest できる period-specific 現象。新規投資の前提条件（持続的 edge）を満たさない。
- **波及する重大な caveat**: Round1 ③（XS reversal）も評価窓が同じ 2025-03〜2026-05 だった。→ 下記で長期再検証済み。

### ③ XS reversal 長期再検証（2026-06-02）

Binance perp 15m を SOL/BTC/ETH(+XRP/DOGE) で 2022-06〜取得（[fetch_binance_klines_15m.py](../research/scripts/fetch_binance_klines_15m.py) → [binance15m/](../research/data/raw/binance15m/), 各 140k bar≈4年）し、xs_rev_L4_H4 をサブ期間分解（[validate_xs_reversal_longwindow.py](../research/scripts/validate_xs_reversal_longwindow.py)）:

| xs_rev_L4_H4 (3銘柄) | full 4年 | 2023 | 2024 | 2025前 | 2025-03+ |
| --- | ---: | ---: | ---: | ---: | ---: |
| gross Sharpe | +1.31 | +0.65 | +1.94 | +3.96 | **+0.76** |
| net 1.5bps Sharpe | -3.74 | -3.97 | -3.85 | -0.40 | -7.32 |

- **C と違い gross は全期間で正＝reversal は持続的なマイクロ構造現象**（period-specific ではない）。
- **だが magnitude が桁違いに小さい**: 元③の GMO/JPY での gross +7.07 に対し、**流動的 Binance では同じ 2025-03+ 期間でも +0.76、4年 full で +1.31**。→ **「+7」は GMO の低流動 JPY 板の bid-ask bounce 固有**で、流動 venue には移植されない（findings の "マイクロ構造/bid-ask bounce 由来" を裏付け）。
- **net 1.5bps で全期間 deeply 負**（full -3.74、最良サブ期間でも -0.40）。流動 venue の gross が小さすぎてコストを一切超えられない。
- **判定: ③ も tradeable edge ではない**。gross は実在するが (a) 低流動 venue 固有で流動 venue では微小、(b) net では全 venue・全期間で負。Round1 ③ の REJECT(net) を長期・他 venue で追認し、「低コスト venue なら取れる」という残された希望も否定（流動 venue では gross 自体が消える）。

## Round 2 全体の結論（A〜D）

| Track | 系統 | 結果 |
| --- | --- | --- |
| A | XS reversal を GMO 実コスト | REJECT（taker 実測 ~7bps、maker 予算 0.5bps で prior 低） |
| B | 低頻度 XS momentum＋拡張 | REJECT（流動コアに edge 無し、edge は illiquid 集中＝取引不能） |
| D | funding tail-filter を v2 LIVE | 安全な marginal+ だが polish で見送り（リスク本体に効かず） |
| **C** | **funding carry basket** | 14ヶ月窓では Done 達成に見えたが、**2.6年に拡張すると out-of-sample で消失（候補3 で判明）。edge は 2025-26 限定の period-specific 現象＝robust でない** |

**収束した構造的事実**: GMO レバレッジの取引可能（流動）ユニバースは高相関 5 メジャーのみで、cross-sectional 系（reversal=A / momentum=B）に必要な breadth・低相関・低コストが構造的に欠如。一方 Binance perp は (1) 529 USDT perp の breadth、(2) maker ~1-2bps の低コスト、(3) funding という GMO に無いデータ次元を持ち、funding carry edge が real かつ取引可能。

**Round 1+2 を通じた edge の所在（候補3 + ③再検証 後の最終改訂）**: 当初「本物の edge 2つ」とした cross-sectional/funding 系は、いずれも長期・他 venue 検証で取引可能性を否定された。
1. **cross-sectional reversal（③）**: gross は全期間持続するが、+7 は GMO 低流動 JPY 板の bid-ask bounce 固有で流動 venue では +0.76〜1.31 に縮小。net は全 venue・全期間で負。→ tradeable でない。
2. **funding carry（C）**: 2.6年で out-of-sample 失敗、edge は 2025-26 限定。β中立後も 2bps maker で alpha≒ゼロ。→ robust でない。

**最終総括**: Round1〜2＋alt-data を通じ、**robust（across regime）かつ net-tradeable な新規 edge は一つも見つからなかった**。見かけ上の edge（③ の +7、C の Done、alt-data の各 signal）は全て、特定 venue のマイクロ構造・特定 favorable 期間・偽相関のいずれかに起因し、proper な長期/他 venue/ネガコン検証で消えた。**plan §撤退条件「①〜全滅 → 新規 edge 探索を凍結、現行 LIVE 維持のみ」に該当**。

唯一の採用 edge は現行 GMO LIVE v2（`direction×hour`、5-day kill-switch 前提の borderline）のまま。今後は新規 directional/structural edge 探索を凍結し、(a) v2 LIVE の運用・リスク管理（kill-switch, サイズ）、(b) もし投資するなら検証方法論そのもの（より長い履歴・複数 venue・厳密なネガコン）への投資、が合理的。安易な gross edge の再発見には乗らない。
