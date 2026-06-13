# gmo_bot Round 4 結果サマリ: GMO spread market-making

> 計画は [gmo_bot_mm_round4_exploration_plan.md](gmo_bot_mm_round4_exploration_plan.md)。

## Phase 0 EV（机上, 2026-06-07）— marginal、かつ受動収集では判定不能

新規収集なしで MM の損益分岐を机上で詰めた（[analyze 計算](../research/scripts/analyze_mm_adverse_selection.py) + EV モデル）。

### 確定パラメータ（live public API）

- 現物 SOL maker rebate **-0.03%（+3bps/fill, 往復 +6bps）**＝確定・構造的な収入。post-only(`SOK`) 保証。spread ~6bps
- SOL/JPY 15m 1σ = 44.9bps → **1σ/秒 ≈ 1.5bps**。requote 遅延 τ=0.5–1s の pick-off 露出 ~1–2bps/move

### 損益分岐面

per-fill net = `rebate3 + half_spread捕捉 − a`、`a = p_toxic × Δ_toxic`。net 正となる toxic-fill 比率 p*:

| Δ_toxic ＼ spread捕捉 | rebateのみ | +1.5bps | +3bps(full half) |
| --- | --- | --- | --- |
| 5bps | 60% | 90% | 100% |
| 10bps | 30% | 45% | 60% |
| 15bps | 20% | 30% | 40% |
| 20bps | 15% | 22% | 30% |

### 判定: retail にとって marginal〜negative

- net 正の条件 = **toxic-fill 比率を ~30–40% 未満**（Δ_toxic 10–15bps が SOL では現実的）
- **HFT**（前列・低レイテンシ）p≈10–20% → 余裕で黒。だから pro はやる
- **retail VPS bot**（後列・高レイテンシ、現物SOL best bid に ~43 SOL が前に並ぶ厚い板 500 levels）p≈40–70% → **損益分岐上＝net ~0〜負**
- 決定変数は**キュー位置／レイテンシ**で構造的に retail 不利。XS reversal と同じ「gross は本物だが realize 不能」の execution-bound パターン

### 最重要: 受動的板収集は go/no-go の決定変数を測れない

p_toxic（**自分の fill の毒性**）は自分のキュー位置・約定の仕方で決まる。受動収集が測るのは市場フローであって自分の realized fill ではない。
→ **HF 板収集パイプライン（forward / ベンダー履歴いずれも）は MM の判定に無効**。realized p を測れる唯一の手段は**極小 live post-only 実発注**（0.01 SOL≈¥100 で両側 quote し実 fill と約定後ドリフトを記録）。

### 結論（Phase 0）

MM は rebate という確定ポジティブで Round 1–3 の予測 edge よりマシだが、retail のキュー/レイテンシ劣位で **EV は marginal〜negative**。**受動収集に投資しても判定できない**ため、選択肢は (1) 極小 live post-only 実験で realized p を直接計測（実注文・要許可）、(2) MM 棚上げ、の二択。passive collection（ローカル/VPS/ベンダー）はいずれも非推奨で実施しない。
