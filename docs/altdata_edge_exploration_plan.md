# 外部データ / クロスドメイン edge 探索計画（alt-data Round）

- 対象: 暗号資産の**低頻度（日次〜週次）方向・レジーム予測**を、OHLCV/funding 以外の**あらゆる外部データ**（他先物・マクロ・フロー・センチメント・果ては野菜価格まで）から探索する
- 位置づけ: Round 1/2（[gmo_bot_edge_round2_findings.md](gmo_bot_edge_round2_findings.md)）で **crypto-native の入力軸（OHLCV/funding）は枯らした**。本計画は「入力の軸を crypto の外へ広げる」最後のフロンティア
- 作成: 2026-06-02

## 0. 出発点（Round 1/2 から引き継ぐ事実）

- crypto-native の本物の edge は 2 つ（XS reversal / funding carry）だけで、いずれも**低コスト多銘柄 venue（Binance perp）が前提**。GMO 流動ユニバースの directional 探索は枯渇。
- **未踏なのは「入力データの次元」**。これまでは価格と funding しか見ていない。マクロ・他資産・フロー・センチメントは原理的に別の α 源になりうる（Phase3-V §4E でも未踏と明記）。
- alt-data はほぼ全て**日次以下の更新頻度**（先物終値・経済指標・ETF フロー・トレンド）。よって本探索は **15m intraday ではなく日次〜週次**の予測に絞る（intraday を alt-data で当てるのは非現実的、かつ intraday OHLCV は枯らし済み）。

## 1. ⚠ 最重要: 反データマイニング規律（本計画の背骨）

「あらゆるデータ」は**偽相関の無限の供給源**である。野菜価格と BTC を 1000 通り試せば必ず「効く」系列が出る。それは edge ではなくノイズ。本探索は **edge を見つける**より **偽陽性を殺す**ことに設計の主眼を置く。以下を全 Track で強制する。

1. **事前登録（pre-registration）**: 特徴量ごとに**テスト前に経済機序を 1 行で書く**。機序が書けない系列は「lottery ticket」として Tier 4 に隔離（§3）。
2. **正直な n_trials 台帳**: 試した（feature × 変換 × horizon × predictand）の**全組合せ**を `research/data/altdata/trial_ledger.csv` に記録し、**DSR の n_trials に全数を入れる**（捨てた試行も数える）。後付けで trial 数を過少申告しない。
3. **3 分割**: train（係数調整）/ validation（特徴選択）/ holdout（**1 度だけ触る**）。holdout は最後まで開けない。**OOS > 0** を必須条件にする（IS だけ良い系列は棄却）。
4. **グループ整合性**: 単一の幸運な特徴ではなく、**同じ機序カテゴリが束で効く**ことを要求（例: 「USD 流動性」系の複数指標が同方向に効く）。単発ヒットは fluke として扱う。
5. **安定性**: 符号・大きさが sub-period 間と walk-forward で安定。期間で符号が反転する特徴は棄却。
6. **コスト生存**: Round 2 の教訓 = gross edge は簡単、**net で取れるか**が本番。検証は最初から実コスト（GMO ~7bps / Binance maker ~1-2bps）で評価。
7. **機序の階層化**: 強い機序（DXY→リスク選好）と無機序（レタス価格）を同列に扱わない（§3 の Tier で重み付け）。

## 2. 予測対象（predictand）

> **既定値（変更可）**: 以下を主対象とする。別案（SOL/JPY 専用フィルタのみ等）が良ければ指示で差し替え。

| 対象 | 内容 | 用途 |
| --- | --- | --- |
| 主: BTC 日次方向/リターン | market β そのもの | risk-on/off レジーム検出、既存戦略の上位ゲート |
| 主: SOL 日次方向/リターン | 現行 LIVE 資産 | v2 への日次レジームフィルタ |
| 副: Binance perp クロスセクション | どの alt が相対的に強いか | alt-data 駆動の XS（Round2 C/③ の venue を活用） |

horizon: 1d / 3d / 7d を基本。用途は (a) 既存戦略の**レジームフィルタ/サイズ調整**、(b) **日次アロケーション**、(c) perp book のオーバーレイ。

## 3. 特徴量ユニバース（機序の強さ順に Tier 化）

### Tier 1 — 強いマクロ/クロスアセット機序（最優先）
crypto は high-β な USD 流動性/リスク資産、という確立した機序を持つもの:
- **USD/流動性**: DXY, USD/JPY, 米実質金利(US2Y/10Y/TIPS), Fed net liquidity（総資産−RRP−TGA）, 銀行準備
- **リスク選好**: SPX/NDX 先物, VIX, ハイイールド信用スプレッド(HYG/OAS)
- **代替/補完資産**: 金(GC), 銅(HG), 原油(CL)
- 機序: グローバル流動性とリスクオンが crypto に波及（多くが日次で先行/同時）

### Tier 2 — crypto 構造/フロー（直接的な需給）
- **オンランプ流動性**: ステーブルコイン時価総額Δ（USDT/USDC）
- **現物 ETF フロー**: BTC/ETH spot ETF net flow
- **取引所フロー/在庫**: exchange net flow, miner reserve（on-chain）
- **デリバ建玉/ターム構造**: aggregate OI, perp-spot basis term structure, **options skew/IV**(Deribit)
- 機序: 直接の需給・ポジショニング。funding(Round2)の近縁で別側面

### Tier 3 — crypto-equity プロキシ & センチメント（弱め・要注意）
- COIN / MSTR / マイナー株（リード/ラグ）, crypto Google Trends, Fear&Greed, ソーシャル/funding センチメント
- 機序: アテンション・リテールフロー。ノイズ多く過学習しやすい

### Tier 4 — 無機序 lottery ticket = **ネガティブコントロール**（野菜価格など）
- **農産物/野菜・穀物価格**（トウモロコシ/小麦/大豆/生鮮）, バルチック海運指数, 天候, 無関係な外国株指数, ランダム系列
- 機序: **基本的に無い**。よってこれらは edge 候補ではなく、**パイプラインの偽陽性率を測る較正器**として使う:
  - Tier 1-3 と**完全に同じパイプライン**を ~30 本の無機序系列に通し、「有意」と出る割合 = しきい値での**家族単位偽陽性率(FDR baseline)**。
  - Tier 1 が「この baseline を有意に超える率」で通って初めて、超過分を本物のシグナルと判断する（permutation/negative-control 法）。
  - ユーザの「野菜価格など、あらゆるリミットを外す」要望は、こうして**正面から・かつ統計的に意味のある形**で取り込む（無機序データが「効いて見えた」ら、それは本物の edge がノイズに埋もれていないかの感度を測る材料になる）。

## 4. データ取得可能性

`.venv` は requests/pandas/numpy のみ（yfinance/fredapi/sklearn 未導入）。多くは **requests 直叩きで取得可**:

| ソース | 取得対象 | 方法 |
| --- | --- | --- |
| Yahoo Finance | 先物/株/FX/商品/指数/農産物 | query API を requests（or yfinance 導入） |
| FRED | マクロ（金利・流動性・スプレッド） | JSON API（要無料 key） |
| Binance | crypto OHLCV/funding/OI | 取得済 + 既存 fetcher |
| CoinGecko 等 | ステーブルコイン mcap | public API |
| Deribit | options IV/skew | public API |
| Google Trends | 検索量 | pytrends 導入 |

→ Track 0 で **日次特徴パネル**（共通日付 index に全系列を整合、lookahead 厳禁＝指標は発表/確定時刻でラグ）を構築。モデルは pandas/numpy の線形・単変量中心（sklearn は必要なら導入、まず単純な手法で過学習を避ける）。

## 5. 探索トラック（EV/コスト順）

### Track 0: 日次特徴パネル + trial 台帳（基盤・必須）
- predictand（BTC/SOL 日次リターン）を既存データから生成。Tier 1-2 の主要系列を取得し共通日付で整合。
- **lookahead 防止**: 各系列を「その時点で入手可能な値」に揃える（経済指標は発表ラグ、終値は翌営業日参照）。
- trial_ledger を初期化。

### Track 1: Tier 1 マクロ/クロスアセット 単変量スクリーン（最優先）
- 各特徴の予測力を univariate で測る: **IC（rank 相関）、符号安定性、OOS リターン**。honest DSR。
- Done: グループとして OOS で IC 安定・DSR が Tier 4 baseline を有意に超過。

### Track 2: Tier 2 crypto 構造/フロー
- ステーブルコイン供給Δ・ETF フロー・OI/basis・options skew を同様に単変量スクリーン。
- Done: 同上。funding(Round2)と直交する増分があるか。

### Track 3: Tier 4 ネガティブコントロール電池（野菜含む）
- Track 1-2 と同一パイプラインを ~30 無機序系列に適用 → **偽陽性率 baseline を確定**。
- これは「edge を探す」Track ではなく**他 Track の判定しきい値を較正する**Track。最初に Track 1 と並走させる。

### Track 4: 生存特徴の多変量結合（厳しい正則化）
- 機序あり & 単変量 OOS 通過 & 安定、を**全て満たした特徴だけ**を少数結合（過学習回避のため特徴数を厳しく制限、正則化必須）。
- holdout を**ここで初めて 1 度開く**。
- Done: holdout で OOS Sharpe/IC が baseline 超 & DSR p<0.10。

### Track 5: シグナルの実装形態（filter / overlay）
- 生存シグナルを (a) v2 の日次レジームフィルタ、(b) perp book のオーバーレイ、(c) 日次アロケーションとして実コストで評価。
- Done: 実コストで net 改善（Gate A 相当）→ Gate B/C へ。

## 6. Done / 撤退条件

| Track | Done | 撤退 |
| --- | --- | --- |
| 1 Tier1 | グループ OOS IC が Tier4 baseline を有意超過 | baseline と区別不能 → マクロに日次 edge 無し |
| 2 Tier2 | 同上、funding と直交する増分あり | 増分無し → フロー系は funding に内包 |
| 3 Tier4 | （baseline 確定が目的） | — |
| 4 結合 | holdout OOS Sharpe>baseline & DSR p<0.10 | holdout で崩れる → 多変量は curve fit |
| 5 実装 | 実コストで net 改善（Gate A） | net で改善せず → 取引不能 |
| 全体 | Tier1-2 のいずれかが Track4/5 まで生存 | 全滅 → alt-data に取引可能 edge 無し、価格/funding 系に集中 |

## 7. 検証ガード（共通）

- **honest DSR**: n_trials = trial_ledger 全数。Tier4 baseline 超過率で本物性を判定。
- **3 分割 + walk-forward**: holdout は Track 4 で 1 度のみ。
- **lookahead 監査**: 全特徴の as-of 整合をコードで保証（経済指標の発表ラグ、タイムゾーン）。
- **コスト生存**: gross でなく実コスト net で判定（Round 2 の最大の教訓）。
- LIVE 投入は Gate A/B/C（[README.md](../README.md) §Backtest validity gates）。

## 8. 制約・インフラ

- engine は OHLCV 専用・単一資産 → alt-data 特徴は**研究側の日次パネル**で評価（Round2 の Track③/B/C と同じ研究側ループ方式）。LIVE 化時のみ外部データ取得 infra（fail-open・as-of 厳守）を実装。
- 新規データ adapter（Yahoo/FRED/CoinGecko を requests でラップ）が要る。まず少数の Tier1 系列で PoC し、有望なら拡張。
- 既定 predictand は §2 の通り（変更可）。
