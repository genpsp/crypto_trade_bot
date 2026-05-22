"""Phase 1 axis-sweep: evaluate v0 / v2-best / Supertrend / Donchian variants
across timeframe & pair axes.

Implements §2.2 (Phase 1) of docs/gmo_bot_post_kill_exploration_plan.md. The
script runs rolling-window backtests on a single bars CSV and prints a
markdown table summarising per-window scaled PnL.

Usage:

    python -m research.scripts.explore_phase1_axis_sweep \\
        --bars research/data/raw/soljpy_1h_to_2026_05.csv \\
        --timeframe 1h \\
        --pair SOL/JPY \\
        --windows 13 --window-bars 700

    # 4h example (smaller window-bars because there are ~6x fewer bars/day)
    python -m research.scripts.explore_phase1_axis_sweep \\
        --bars research/data/raw/soljpy_4h_to_2026_05.csv \\
        --timeframe 4h --windows 8 --window-bars 300

Done basis (per plan §2.2):
- pos_rate >= 65% AND mean >= +4 AND min >= -5  → accept (proceed Phase 2)
- pos_rate >= 60% AND mean >= +3 AND min >= -5  (Supertrend/Donchian) → accept
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config


@dataclass(frozen=True)
class _Variant:
    name: str
    strategy_name: str
    components: dict[str, Any] | None  # None → no components key
    extra_strategy_params: dict[str, Any] | None = None


def _build_variants() -> list[_Variant]:
    return [
        _Variant("v0_baseline", "ema_trend_pullback_15m_v0", components=None),
        _Variant(
            "v2_default_bundle",
            "ema_trend_pullback_15m_v2",
            components={},
        ),
        _Variant(
            "v2_D1_Volume_1_2x",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {"type": "volume_confirmed", "volume_multiplier": 1.2}
            },
        ),
        _Variant(
            "v2_B5+A4",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "equity_curve",
                    "lookback_trades": 20,
                    "min_trades": 10,
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        _Variant(
            "supertrend_default",
            "supertrend_15m_v0",
            components=None,
            extra_strategy_params={
                "supertrend_period": 10,
                "supertrend_atr_multiple": 3.0,
            },
        ),
        _Variant(
            "donchian_default",
            "donchian_breakout_15m_v0",
            components=None,
            extra_strategy_params={"donchian_period": 20, "atr_period": 14},
        ),
        _Variant(
            "mean_reversion_default",
            "mean_reversion_15m_v0",
            components=None,
            extra_strategy_params={
                "bb_period": 20,
                "bb_num_std": 2.0,
                "adx_period": 14,
                "adx_chop_max": 25.0,
                "atr_period": 14,
                "stop_atr_cushion": 0.5,
                "long_atr_pct_max": 1.5,
                "short_atr_pct_max": 1.5,
            },
        ),
        _Variant(
            "mean_reversion_bb30_chop20",
            "mean_reversion_15m_v0",
            components=None,
            extra_strategy_params={
                "bb_period": 30,
                "bb_num_std": 2.0,
                "adx_chop_max": 20.0,
                "stop_atr_cushion": 0.7,
            },
        ),
        _Variant(
            "mean_reversion_bb20_std2_5",
            "mean_reversion_15m_v0",
            components=None,
            extra_strategy_params={
                "bb_period": 20,
                "bb_num_std": 2.5,
                "adx_chop_max": 25.0,
                "stop_atr_cushion": 0.5,
            },
        ),
        # Post-mortem F-combo-1: session (JST<18 = UTC{15..8}) + vol>=0.4 + atr>=0.36
        _Variant(
            "v2_postmortem_F_combo_1",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "session",
                            "allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "atr_pct_range",
                            "period": 14,
                            "min_atr_pct": 0.36,
                            "max_atr_pct": 100.0,
                        },
                    ],
                }
            },
        ),
        # Tighter: also require atr_pct >= 0.46 (top 2 quintiles)
        _Variant(
            "v2_postmortem_tight_atr",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "session",
                            "allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "atr_pct_range",
                            "period": 14,
                            "min_atr_pct": 0.46,
                            "max_atr_pct": 100.0,
                        },
                    ],
                }
            },
        ),
        # ATR-only (drop session + volume) — isolate ATR contribution
        _Variant(
            "v2_atr_only_0_46",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "atr_pct_range",
                    "period": 14,
                    "min_atr_pct": 0.46,
                    "max_atr_pct": 100.0,
                }
            },
        ),
        # Vol-only
        _Variant(
            "v2_vol_only_0_4",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.4,
                }
            },
        ),
        # Session-only (JST<18)
        _Variant(
            "v2_session_only_jst<18",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "session",
                    "allowed_utc_hours": [
                        15, 16, 17, 18, 19, 20, 21, 22, 23,
                        0, 1, 2, 3, 4, 5, 6, 7, 8,
                    ],
                }
            },
        ),
        # Tight ATR + session, no volume
        _Variant(
            "v2_session+atr_0_46",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "session",
                            "allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                        },
                        {
                            "type": "atr_pct_range",
                            "period": 14,
                            "min_atr_pct": 0.46,
                            "max_atr_pct": 100.0,
                        },
                    ],
                }
            },
        ),
        # Tight ATR + session + vol + A4 time exit
        _Variant(
            "v2_combo+A4_time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "session",
                            "allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "atr_pct_range",
                            "period": 14,
                            "min_atr_pct": 0.46,
                            "max_atr_pct": 100.0,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # Vol-only tuning sweep
        _Variant(
            "v2_vol_0_3",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.3,
                }
            },
        ),
        _Variant(
            "v2_vol_0_5",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.5,
                }
            },
        ),
        _Variant(
            "v2_vol_0_6",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.6,
                }
            },
        ),
        _Variant(
            "v2_vol_0_8",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.8,
                }
            },
        ),
        # Vol + BE break-even exit at 1R (limit big losses by locking in)
        _Variant(
            "v2_vol_0_4+BE_1R",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.4,
                },
                "exit_policy": {"type": "break_even", "trigger_r": 1.0},
            },
        ),
        # Vol + time exit to cap drawdown bars
        _Variant(
            "v2_vol_0_4+time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "volume_confirmed",
                    "period": 20,
                    "volume_multiplier": 0.4,
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # Vol + ATR upper cap (don't trade extreme storm bars)
        _Variant(
            "v2_vol_0_4+atr<0_85",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "atr_pct_range",
                            "period": 14,
                            "min_atr_pct": 0.0,
                            "max_atr_pct": 0.85,
                        },
                    ],
                }
            },
        ),
        # Vol + Equity curve gate (pause after consecutive losses)
        _Variant(
            "v2_vol_0_4+equity20",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "equity_curve",
                            "lookback_trades": 20,
                            "min_trades": 10,
                        },
                    ],
                }
            },
        ),
        # Vol + Equity curve + time exit
        _Variant(
            "v2_vol_0_4+equity20+time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "equity_curve",
                            "lookback_trades": 20,
                            "min_trades": 10,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # Vol + shorter equity curve lookback (faster pause)
        _Variant(
            "v2_vol_0_4+equity10",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "equity_curve",
                            "lookback_trades": 10,
                            "min_trades": 5,
                        },
                    ],
                }
            },
        ),
        # BTC momentum filter alone
        _Variant(
            "v2_btc_mom_only",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "btc_momentum",
                    "lookback_bars": 4,
                    "min_abs_return_pct": 0.3,
                }
            },
        ),
        # vol_0_4 + BTC momentum
        _Variant(
            "v2_vol_0_4+btc_mom",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "btc_momentum",
                            "lookback_bars": 4,
                            "min_abs_return_pct": 0.3,
                        },
                    ],
                }
            },
        ),
        # vol_0_4 + BTC mom + time120 (full combo)
        _Variant(
            "v2_vol_0_4+btc_mom+time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "btc_momentum",
                            "lookback_bars": 4,
                            "min_abs_return_pct": 0.3,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # BTC mom tighter threshold (0.5)
        _Variant(
            "v2_vol_0_4+btc_mom_0_5",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "btc_momentum",
                            "lookback_bars": 4,
                            "min_abs_return_pct": 0.5,
                        },
                    ],
                }
            },
        ),
        # Direction-aware time-of-day filter
        # Post-mortem cross-tab:
        #   LONG  × evening (JST 18-24 = UTC 9-14): WR 32%, mean -0.14 → DROP
        #   SHORT × morning (JST 06-12 = UTC 21,22,23,0,1,2): WR 27%, mean -0.31 → DROP
        # Keep all other (direction, hour) cells.
        _Variant(
            "v2_dir_session",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "directional_session",
                    # LONG allowed UTC = NOT in [9,10,11,12,13,14] (= JST 18-23)
                    "long_allowed_utc_hours": [
                        15, 16, 17, 18, 19, 20, 21, 22, 23,
                        0, 1, 2, 3, 4, 5, 6, 7, 8,
                    ],
                    # SHORT allowed UTC = NOT in [21,22,23,0,1,2] (= JST 06-11)
                    "short_allowed_utc_hours": [
                        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                    ],
                }
            },
        ),
        # Direction-aware + vol filter
        _Variant(
            "v2_dir_session+vol",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                    ],
                }
            },
        ),
        # Direction-aware + vol + BTC momentum (full combo)
        _Variant(
            "v2_dir_session+vol+btc_mom",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "btc_momentum",
                            "lookback_bars": 4,
                            "min_abs_return_pct": 0.3,
                        },
                    ],
                }
            },
        ),
        # Full combo + time120 exit
        _Variant(
            "v2_dir_session+vol+btc_mom+time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                        {
                            "type": "btc_momentum",
                            "lookback_bars": 4,
                            "min_abs_return_pct": 0.3,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # dir_session+vol with break-even at 1R (cap loss after favorable move)
        _Variant(
            "v2_dir_session+vol+BE_1R",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                    ],
                },
                "exit_policy": {"type": "break_even", "trigger_r": 1.0},
            },
        ),
        # dir_session+vol + time120
        _Variant(
            "v2_dir_session+vol+time120",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": False,
                },
            },
        ),
        # dir_session+vol + composite exit (BE then time)
        _Variant(
            "v2_dir_session+vol+BE+time",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "composite",
                    "policies": [
                        {"type": "break_even", "trigger_r": 1.0},
                        {
                            "type": "time_exit",
                            "max_holding_bars": 120,
                            "prefer_breakeven": True,
                        },
                    ],
                },
            },
        ),
        # dir_session+vol + partial TP at 1R (lock partial profit, runner to 2R)
        _Variant(
            "v2_dir_session+vol+partial_1R",
            "ema_trend_pullback_15m_v2",
            components={
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {
                            "type": "directional_session",
                            "long_allowed_utc_hours": [
                                15, 16, 17, 18, 19, 20, 21, 22, 23,
                                0, 1, 2, 3, 4, 5, 6, 7, 8,
                            ],
                            "short_allowed_utc_hours": [
                                3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                            ],
                        },
                        {
                            "type": "volume_confirmed",
                            "period": 20,
                            "volume_multiplier": 0.4,
                        },
                    ],
                },
                "exit_policy": {
                    "type": "composite",
                    "policies": [
                        {
                            "type": "partial_tp",
                            "partial_r": 1.0,
                            "partial_fraction": 0.5,
                        },
                        {"type": "break_even", "trigger_r": 1.0},
                    ],
                },
            },
        ),
    ]


def _make_config(
    base_config: dict[str, Any],
    variant: _Variant,
    timeframe: str,
    pair: str,
    execution_model: str = "ideal_v1",
    execution_profile_path: str | None = None,
    execution_seed: int | None = None,
) -> dict[str, Any]:
    config = json.loads(json.dumps(base_config))
    config["pair"] = pair
    config["signal_timeframe"] = timeframe
    config["execution"] = dict(config.get("execution", {}))
    config["execution"]["model_id"] = execution_model
    if execution_profile_path is not None:
        config["execution"]["profile_path"] = execution_profile_path
    if execution_seed is not None:
        config["execution"]["seed"] = execution_seed
    config["strategy"] = dict(config["strategy"])
    config["strategy"]["name"] = variant.strategy_name
    if variant.extra_strategy_params:
        for key, value in variant.extra_strategy_params.items():
            config["strategy"][key] = value
    if variant.components is not None:
        config["strategy"]["components"] = variant.components
    return config


def _evaluate_window(
    variant: _Variant,
    bars,
    base_config,
    timeframe: str,
    pair: str,
    execution_model: str = "ideal_v1",
    execution_profile_path: str | None = None,
    execution_seed: int | None = None,
) -> dict[str, Any]:
    config = _make_config(
        base_config,
        variant,
        timeframe,
        pair,
        execution_model=execution_model,
        execution_profile_path=execution_profile_path,
        execution_seed=execution_seed,
    )
    report = run_backtest(bars=bars, config=config)
    closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
    sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
    wins = sum(1 for trade in closed if (trade.pnl_pct or 0) > 0)
    wr = (wins / len(closed) * 100) if closed else 0.0
    return {
        "scaled_pnl_pct": round(sum_scaled, 4),
        "trades": len(closed),
        "win_rate_pct": round(wr, 2),
    }


def _decide_done(variant_name: str, summary: dict[str, Any]) -> str:
    pos_rate = summary["pos_rate_pct"]
    mean = summary["mean"]
    win_min = summary["min"]
    is_alt = variant_name.startswith(("supertrend", "donchian", "mean_reversion"))
    if is_alt:
        target = "pos_rate>=60 mean>=+3 min>=-5"
        ok = pos_rate >= 60.0 and mean >= 3.0 and win_min >= -5.0
    else:
        target = "pos_rate>=65 mean>=+4 min>=-5"
        ok = pos_rate >= 65.0 and mean >= 4.0 and win_min >= -5.0
    return "ACCEPT" if ok else f"REJECT ({target})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", required=True)
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument("--timeframe", required=True, choices=["15m", "1h", "2h", "4h"])
    parser.add_argument("--pair", default="SOL/JPY")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--window-bars", type=int, default=700)
    parser.add_argument(
        "--variants",
        nargs="*",
        default=None,
        help="optional subset of variants by name",
    )
    parser.add_argument("--output-json", default=None)
    parser.add_argument(
        "--execution-model",
        default="ideal_v1",
        choices=["ideal_v1", "pessimistic_v1", "stochastic_v1"],
    )
    parser.add_argument("--execution-profile", default=None)
    parser.add_argument("--execution-seed", type=int, default=None)
    args = parser.parse_args()

    base_config = load_bot_config(args.base_config)
    all_bars = read_bars_from_csv(args.bars)
    total_needed = args.windows * args.window_bars
    if total_needed > len(all_bars):
        raise SystemExit(
            f"need {total_needed} bars but CSV has {len(all_bars)}; "
            "lower --windows or --window-bars"
        )
    all_bars = all_bars[-total_needed:]
    attach_regime_tags(all_bars)

    variants = _build_variants()
    if args.variants:
        wanted = set(args.variants)
        variants = [v for v in variants if v.name in wanted]
        missing = wanted - {v.name for v in variants}
        if missing:
            raise SystemExit(f"unknown variants: {sorted(missing)}")

    per_variant_windows: dict[str, list[dict[str, Any]]] = {v.name: [] for v in variants}

    for window_index in range(args.windows):
        start = window_index * args.window_bars
        end = start + args.window_bars
        bars = all_bars[start:end]
        if len(bars) < args.window_bars:
            break
        first_open = bars[0].open_time.isoformat().replace("+00:00", "Z")
        last_open = bars[-1].open_time.isoformat().replace("+00:00", "Z")
        for variant in variants:
            row = _evaluate_window(
                variant,
                bars,
                base_config,
                args.timeframe,
                args.pair,
                execution_model=args.execution_model,
                execution_profile_path=args.execution_profile,
                execution_seed=args.execution_seed,
            )
            row["window_index"] = window_index
            row["first_open"] = first_open
            row["last_open"] = last_open
            per_variant_windows[variant.name].append(row)

    print(
        f"\n## Phase 1 axis-sweep — pair={args.pair} timeframe={args.timeframe} "
        f"windows={args.windows} window_bars={args.window_bars}\n"
    )
    headers = (
        ["variant"]
        + [f"w{i}" for i in range(args.windows)]
        + ["min", "mean", "pos_rate%", "verdict"]
    )
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")

    summary: list[dict[str, Any]] = []
    for variant in variants:
        rows = per_variant_windows[variant.name]
        pnls = [r["scaled_pnl_pct"] for r in rows]
        if not pnls:
            continue
        win_min = min(pnls)
        win_mean = sum(pnls) / len(pnls)
        pos_rate = sum(1 for v in pnls if v > 0) / len(pnls) * 100
        variant_summary = {
            "variant": variant.name,
            "per_window": rows,
            "min": round(win_min, 4),
            "mean": round(win_mean, 4),
            "pos_rate_pct": round(pos_rate, 2),
        }
        verdict = _decide_done(variant.name, variant_summary)
        variant_summary["verdict"] = verdict
        cells = [variant.name] + [f"{v:+.2f}" for v in pnls] + [
            f"{win_min:+.2f}",
            f"{win_mean:+.2f}",
            f"{pos_rate:.1f}",
            verdict,
        ]
        print("| " + " | ".join(cells) + " |")
        summary.append(variant_summary)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(
                {
                    "pair": args.pair,
                    "timeframe": args.timeframe,
                    "windows": args.windows,
                    "window_bars": args.window_bars,
                    "bars_path": args.bars,
                    "variants": summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[phase1-axis] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
