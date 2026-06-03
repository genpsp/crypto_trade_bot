# 外部データ / クロスドメイン edge 探索 結果サマリ

> 計画は [altdata_edge_exploration_plan.md](altdata_edge_exploration_plan.md)。
> Track 0(日次パネル)+1(Tier1 マクロ単変量)+3(ネガティブコントロール較正)+4(合成タイミング) を実施。

## データ（Track 0）

Yahoo Finance から 35 系列（2021-01〜2026-06）= [fetch_altdata_yahoo.py](../research/scripts/fetch_altdata_yahoo.py) → `research/data/raw/altdata/`。
predictand 3（BTC/SOL/ETH）/ Tier1 マクロ 13 / Tier3 crypto-equity 2 / Tier4 ネガコン 17。
共通平日で整合し、特徴は **lag1（t の特徴で t+1→t+1+h を予測）で lookahead 防止**。全試行を [trial_ledger.csv](../research/data/altdata/trial_ledger.csv) に記録（honest n_trials=192/predictand）。

## Track 1+3: 単変量 IC スクリーン + ネガコン較正（BTC, h=1/5）

### Tier 別「有意」率（真の null=農産物を baseline に）

| group | 試行 | sig(p<.05) | sig率 | 解釈 |
| --- | ---: | ---: | ---: | --- |
| **Tier1 マクロ** | 78 | 11 | **14%** | 真 null の ~4.7倍 = 本物の予測情報あり |
| Tier3 proxy（COIN/MSTR） | 12 | 3 | 25% | crypto 同値物（循環的） |
| Tier4b 株式（KO/PG/XLU 等） | 42 | 4 | 10% | **市場β=リスク因子保有→真の null でない** |
| **Tier4a 農産物（真 null）** | 60 | 2 | **3%** | パイプラインの経験的偽陽性 baseline |

- **ネガコンが本質的に機能**: |IC| 最大は **KO(コカ・コーラ) z20→BTC h5: IC -0.117 / OOS test -0.239** という Tier4 系列だった。素朴に |IC| 順で選べば「コカ・コーラが BTC を予測」と誤発見していた。
- 重要な精緻化: Tier4 を「農産物（真の無機序）」と「株式（市場β保有）」に分けると、真 null は 3%、株式は 10%（β経由で BTC とリスク因子を共有）。マクロ 14% は真 null 3% を明確に超える。
- 機序整合の生存特徴: **SPX/NDX の 5d momentum → BTC 5d リターン**（IC +0.10/+0.09、sign_stab 1.0、IC_test +0.13/+0.14）。equity risk-on が最も頑健。金利(US10Y chg, IC -0.08)は符号は機序通りだが OOS で減衰。

## Track 4: 取引可能性 — マクロは weak association だが robust な tradeable edge にならない

### 単一最強機序の timing backtest（5d momentum 符号で BTC 日次 long/short）

| signal | IS Sharpe | OOS Sharpe |
| --- | ---: | ---: |
| SPX→BTC（機序・最強） | +0.96 | **-0.64** |
| KO→BTC（ネガコン） | -0.01 | -1.20 |

→ IC で最強の機序特徴ですら、naive な日次タイミングでは **OOS で崩れる**（Round2 と同じ「weak association ≠ tradeable edge」）。

### リスクオン合成（SPX/NDX/VIX/US10Y/HYG を機序整合 z-score 平均）

| signal | L/S IS | L/S OOS | L/flat full |
| --- | ---: | ---: | ---: |
| risk_on 合成（機序5本） | +1.12 | +1.30 | +0.92 |
| 農産物合成（ネガコン5本） | +0.23 | **+1.65** | +0.47 |
| BTC buy&hold | — | — | +0.08 |

- **赤信号**: ネガコン（農産物合成）も OOS Sharpe +1.65 を出した。= holdout(後30%≈直近1年)が単一レジームで、**約5年の日次データでは日次マクロタイミングを頑健に検証する検出力が無い**。
- L/flat では risk_on(+0.92) > ネガコン(+0.47) > buy&hold(+0.08) でマクロは一応上回るが、単一 split は信頼できない。**60本のネガコンで baseline を測る IC スクリーンの方が検出力が高い**。

## 結論（alt-data Track1/3/4）

1. **方法論は成功**: ネガティブコントロール（KO 単体・農産物合成の OOS+1.65）が、素朴な選択・単一 split 検証が偽 edge を量産することを実証。計画の背骨（反データマイニング規律）が「マクロが BTC を予測する」誤発見を防いだ。
2. **マクロは本物だが weak**: equity risk-on（SPX/NDX 5d momentum）が真 null baseline(3%) を超える予測情報を持つ（IC~0.10、sign 安定）。だが crypto 自身の vol に対し小さく、**standalone の tradeable directional edge にはならない**（SPX→BTC OOS Sharpe 負）。
3. **データ長の壁**: 日次×~5年では daily macro timing を頑健に検証できない（ネガコンも OOS で勝つ）。検証可能にするには (a) より長い履歴、(b) 多数特徴の population 統計（IC スクリーン）、(c) 高 SNR な crypto-native 構造データ、のいずれかが要る。
4. **全体テーマと整合**: crypto の tradeable edge は構造/マイクロ構造（funding carry, XS）であって macro-directional-prediction ではない。マクロは crypto を**同時的に説明**する（high β）が、日次で**取引可能に予測**はしない。

## Track 2: crypto 構造/フロー（2026-06-02）

[fetch_crypto_structural.py](../research/scripts/fetch_crypto_structural.py) で取得し同一 screen に統合:
STABLES（USD ステーブル総時価, DefiLlama 全履歴）/ DVOL_BTC・ETH（Deribit 実装ボラ, 2021-03〜）を主パネル（tier2）、AGGFUND（20perp 集計funding 日次平均, 2025-03〜と短いので別途）。

### IC スクリーン（BTC, 同じ ag-null baseline 3% で比較）

| group | 試行 | sig率 | OOS一致 |
| --- | ---: | ---: | ---: |
| Tier2 crypto構造 | 18 | **33%（全 tier 最高）** | 28% |
| Tier1 マクロ | 78 | 14% | 40% |
| Tier4a 農産物（真 null） | 60 | 3% | — |

- **IS association は最強だが OOS で分かれる**:
  - **DVOL z20→BTC h5: IS IC +0.147 → OOS -0.038（符号反転）**。実装ボラの逆張りは 2021-22 高ボラ regime の overfit、post-2023 で消失。
  - **STABLES chg1→BTC: IC_train +0.081 / IC_test +0.032（符号維持・stab 0.8）**。弱いが OOS 一貫。

### 取引可能性 probe

| signal | IS | OOS | 備考 |
| --- | ---: | ---: | --- |
| **STABLES 5d供給増→BTC（L/S）** | **+0.64** | **+0.70** | **alt-data 全体で唯一 OOS が崩れない**。機序明快（供給増=買い余力） |
| AGGFUND 集計funding 逆張り→BTC（L/S） | +1.37（full のみ） | — | 458日/単一窓/OOS無し。buy&hold 同窓 -0.35 で守備的 signal が有利なだけの懸念大＝**検証不能** |

- **STABLES が alt-data 全体の最有力 survivor**: SPX(OOS -0.64)・DVOL(符号反転) が OOS で崩れる中、唯一 IS≈OOS（+0.64/+0.70）で機序も明快。ただし絶対水準は弱く、ネガコン合成が OOS+1.65 を出した前例（Track4）から、proper な negative-control ベンチマーク＋複数窓検証なしに edge 確定はできない。
- AGGFUND は C(cross-sectional funding carry)の時系列版として機序は魅力的だが、funding 履歴が 15ヶ月しかなく検証不能。履歴蓄積後に再訪。

## 結論（alt-data Round 全体: Track1/2/3/4）

1. **方法論（反データマイニング規律）が最大の成果**: ネガティブコントロールが KO(コカ・コーラ)の偽 IC・農産物合成の OOS+1.65 を炙り出し、素朴な探索が量産する偽 edge を体系的に棄却。「あらゆるデータ」を無秩序でなく統計的に扱えた。
2. **予測情報の階層**: 真 null 3% < マクロ 14% < crypto構造 33%（IS）。情報量は crypto-native ほど大きいが、**OOS 頑健性は逆**で、強い IS ほど regime overfit（DVOL）。
3. **唯一 OOS で崩れない alt-data signal = STABLES（ステーブル供給増）**。弱いが機序明快・OOS一貫。standalone edge には弱いが、**risk/liquidity レジームフィルタ**の最有力候補。
4. **データ長の壁が全体を貫く**: 日次×~5年（暗号資産では実質1-2 regime）では directional timing を頑健に検証できない。alt-data の真価は long-horizon の population 統計か、より高頻度な crypto 構造データの蓄積待ち。
5. **全体テーマと整合**: tradeable な crypto edge は構造/マイクロ構造（funding carry C, XS reversal ③）。外部データは crypto を*説明*するが日次で*取引可能に予測*はしない。alt-data の最も現実的な貢献は STABLES/AGGFUND 系の**フィルタ/レジーム**用途。

## 候補1+2 の proper 検証（2026-06-02）— 両方 REJECT

先の「STABLES OOS+0.70 / AGGFUND +1.37」が本物か、ネガコン法＋置換検定で厳格に検証した。

### 候補1: STABLES（[validate_stables_signal.py](../research/scripts/validate_stables_signal.py)）

同一 signal を STABLES と農産物 null に適用、STABLES の percentile で判定（2021-2026, 1358日, 平日整合）:

| mode | STABLES full Sharpe | 農産物null 中央値 | percentile |
| --- | ---: | ---: | ---: |
| ls_growth（5d成長符号 L/S） | -0.17 | -0.16 | **50%（=null）** |
| ls_z（60d脱トレンド L/S） | +0.28 | -0.25 | 80%（p~0.2 非有意） |

- 先の「OOS+0.70」は**週末込みサンプル＋単一 split のアーティファクト**。農産物 null を同一処理で並べると STABLES は区別不能（成長符号で 50%ile）。脱トレンド版は 80%ile だが非有意（null max +0.88 > STABLES +0.28）。→ **REJECT**。

### 候補2: AGGFUND（[validate_aggfund_signal.py](../research/scripts/validate_aggfund_signal.py)）

funding_long（20 perp ~2021〜, 5936点/perp）で日次集計funding を再構築、逆張りタイミングを循環シフト置換検定＋農産物 null で検証:

| predictand | full Sharpe | 循環シフト置換 p | 農産物null percentile |
| --- | ---: | ---: | ---: |
| BTC | -0.04 | 0.565 | 40%（=null） |
| ETH | +0.06 | 0.461 | 70% |
| SOL | -0.34 | 0.736 | 30% |

- 先の「+1.37」「OOS+1.12」は 458日単一窓のアーティファクト。5年＋置換検定では null と区別不能（p~0.46-0.74）。→ **REJECT**。
- **重要な対比**: Round2 C の**クロスセクション** funding carry（相対funding）は real edge だが、**時系列**集計funding（市場全体のタイミング）は edge 無し。相対funding 分散は収穫可能、絶対水準は方向を当てない、という機序的に筋の通る区別。

### 含意

- alt-data Round で見かけ上有望だった全 signal（KO の偽 IC, SPX timing, DVOL, STABLES, AGGFUND）が **proper 検証で例外なく棄却**。ネガコン法＋置換検定が一貫して偽 edge を炙り出した。
- **確定結論: 外部データ（マクロ/フロー/センチメント/野菜）から暗号資産の日次方向を取引可能に予測する edge は見つからない**。crypto を*説明*はするが*予測*はしない。tradeable edge は構造/マイクロ構造系（cross-sectional funding carry / reversal）に限られる。
- Track 5（filter 用途）も母体 signal が null と区別不能なため見送り。次の投資は Round2 C（funding carry）の実装前進が唯一の生存路線。
