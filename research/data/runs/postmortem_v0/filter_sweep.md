# Filter sweep — temporal IS/OOS

- Total trades: **624** (IS = first 312, OOS = last 312)
- Source: `research/data/runs/postmortem_v0/trade_features.csv`

| filter | expr | all_n | all_wr | all_mean | all_sum | IS_n | IS_wr | IS_mean | IS_sum | OOS_n | OOS_wr | OOS_mean | OOS_sum |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | (none) | 624 | 39.26 | 0.10 | 63.29 | 312 | 42.63 | 0.20 | 63.48 | 312 | 35.90 | -0.00 | -0.19 |
| F-hour-not-evening | jst_hour < 18 | 479 | 40.92 | 0.16 | 74.80 | 241 | 43.98 | 0.25 | 60.45 | 238 | 37.82 | 0.06 | 14.34 |
| F-hour-deep-night-only | jst_hour < 6 | 135 | 45.93 | 0.35 | 47.84 | 68 | 48.53 | 0.43 | 29.15 | 67 | 43.28 | 0.28 | 18.69 |
| F-vol-ratio>=0.4 | volume_ratio_20 >= 0.4 | 358 | 42.46 | 0.18 | 66.29 | 174 | 43.68 | 0.23 | 40.22 | 184 | 41.30 | 0.14 | 26.07 |
| F-vol-ratio>=0.36 | volume_ratio_20 >= 0.36 | 372 | 43.01 | 0.20 | 74.80 | 184 | 44.02 | 0.24 | 44.24 | 188 | 42.02 | 0.16 | 30.56 |
| F-atr>=0.36 | atr_pct >= 0.36 | 375 | 41.07 | 0.16 | 60.65 | 231 | 43.72 | 0.25 | 56.99 | 144 | 36.81 | 0.03 | 3.66 |
| F-atr>=0.46 | atr_pct >= 0.46 | 254 | 42.91 | 0.22 | 55.09 | 162 | 45.68 | 0.31 | 50.14 | 92 | 38.04 | 0.05 | 4.95 |
| F-btc-4bar-abs>=0.3 | abs(btc_ret_4bar_pct) >= 0.3 | 218 | 46.79 | 0.34 | 74.81 | 98 | 53.06 | 0.56 | 54.61 | 120 | 41.67 | 0.17 | 20.19 |
| F-adx-16-32 | 16 <= adx <= 32 | 367 | 42.78 | 0.19 | 70.21 | 185 | 47.03 | 0.34 | 63.49 | 182 | 38.46 | 0.04 | 6.72 |
| F-combo-1 | jst_hour < 18 and volume_ratio_20 >= 0.4 and atr_pct >= 0.36 | 166 | 48.80 | 0.41 | 68.78 | 98 | 46.94 | 0.36 | 35.81 | 68 | 51.47 | 0.48 | 32.97 |
| F-combo-2 | jst_hour < 18 and volume_ratio_20 >= 0.4 and atr_pct >= 0.36 and abs(btc_ret_4bar_pct) >= 0.3 | 73 | 57.53 | 0.70 | 51.29 | 38 | 55.26 | 0.65 | 24.51 | 35 | 60.00 | 0.77 | 26.77 |
| F-combo-3 | jst_hour < 18 and atr_pct >= 0.36 and 16 <= adx <= 32 | 179 | 46.93 | 0.36 | 64.31 | 104 | 50.96 | 0.50 | 52.24 | 75 | 41.33 | 0.16 | 12.07 |
| F-dir-tod | (direction == 'LONG' and jst_hour < 18) or (direction == 'SHORT' and jst_hour < 6) | 334 | 44.31 | 0.27 | 89.60 | 178 | 46.07 | 0.31 | 55.37 | 156 | 42.31 | 0.22 | 34.23 |
| F-dir-tod-loose | (direction == 'LONG' and jst_hour < 18) or (direction == 'SHORT' and (jst_hour < 6 or 12 <= jst_hour < 18)) | 415 | 43.13 | 0.23 | 94.44 | 214 | 45.79 | 0.30 | 64.04 | 201 | 40.30 | 0.15 | 30.41 |
| F-dir-tod+vol+atr | ((direction == 'LONG' and jst_hour < 18) or (direction == 'SHORT' and jst_hour < 6)) and volume_ratio_20 >= 0.4 and atr_pct >= 0.36 | 122 | 53.28 | 0.56 | 68.81 | 75 | 49.33 | 0.44 | 33.33 | 47 | 59.57 | 0.76 | 35.48 |