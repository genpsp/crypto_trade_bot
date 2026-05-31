"""Concrete RegimeGate implementations. Track B candidates live here.

A RegimeGate runs once per bar BEFORE the entry signal is evaluated; returning
False prevents entry on that bar with a gate-specific reason.

The engine maintains a small `gate_state` dict that gates can read / write
across bars (e.g. EquityCurveGate keeps a rolling tally). Stateless gates
ignore it.

ADX / Donchian / ATR computations are O(N) over the bar list. To avoid
re-running them per-bar, the gates cache results keyed by `id(bars)` in
module-level dicts. The lookup hits a precomputed array indexed by bar index.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC as UTC_TZ
from typing import Any

from apps.dex_bot.domain.model.types import OhlcvBar
from apps.gmo_bot.domain.strategy.components.base import RegimeGate


# Module-level caches keyed by (id(bars), params). Cleared implicitly when
# bar lists go out of scope (id reuse risk is mitigated by also keying on
# len(bars) — collisions across same-id-same-length lists are vanishingly
# rare for our usage).
_ADX_CACHE: dict[tuple[int, int, int], list[float]] = {}
_DONCHIAN_WIDTH_CACHE: dict[tuple[int, int, int, int], list[float]] = {}
_ATR_CACHE: dict[tuple[int, int, int], list[float]] = {}


@dataclass(frozen=True)
class NullRegimeGate(RegimeGate):
    """No-op gate. Used as the default for the v2 strategy so its default
    component bundle reproduces v0 behaviour."""

    name: str = "null_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        return True


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Classic Wilder smoothing (RMA): first value = SMA of the initial period,
    subsequent values use new = previous * (period-1)/period + value/period."""
    n = len(values)
    if n == 0 or period <= 0:
        return [0.0] * n
    smoothed: list[float] = [0.0] * n
    running = 0.0
    for index in range(n):
        if index < period - 1:
            running += values[index]
        elif index == period - 1:
            running += values[index]
            smoothed[index] = running / period
        else:
            previous = smoothed[index - 1]
            smoothed[index] = previous - (previous / period) + (values[index] / period)
    return smoothed


def _compute_adx_series(bars: list[OhlcvBar], period: int) -> list[float]:
    """Return ADX value per bar index. NaN-equivalent (0.0) before warm-up."""
    n = len(bars)
    if n == 0:
        return []
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    tr_s = _wilder_smooth(tr, period)
    plus_dm_s = _wilder_smooth(plus_dm, period)
    minus_dm_s = _wilder_smooth(minus_dm, period)
    dx: list[float] = [0.0] * n
    for i in range(n):
        if tr_s[i] <= 0:
            continue
        plus_di = 100 * plus_dm_s[i] / tr_s[i]
        minus_di = 100 * minus_dm_s[i] / tr_s[i]
        denom = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
    return _wilder_smooth(dx, period)


def _get_adx_series(bars: list[OhlcvBar], period: int) -> list[float]:
    key = (id(bars), len(bars), period)
    cached = _ADX_CACHE.get(key)
    if cached is not None:
        return cached
    series = _compute_adx_series(bars, period)
    _ADX_CACHE[key] = series
    return series


def _compute_atr_series(bars: list[OhlcvBar], period: int) -> list[float]:
    n = len(bars)
    if n == 0:
        return []
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    # Simple moving ATR over `period` bars
    result: list[float] = [0.0] * n
    running = 0.0
    for i in range(n):
        running += tr[i]
        if i >= period:
            running -= tr[i - period]
        if i >= period - 1:
            result[i] = running / period
    return result


def _get_atr_series(bars: list[OhlcvBar], period: int) -> list[float]:
    key = (id(bars), len(bars), period)
    cached = _ATR_CACHE.get(key)
    if cached is not None:
        return cached
    series = _compute_atr_series(bars, period)
    _ATR_CACHE[key] = series
    return series


def _compute_donchian_width_series(
    bars: list[OhlcvBar], donchian_period: int, atr_period: int
) -> list[float]:
    """Return Donchian-width / ATR ratio per bar index. 0 before warm-up."""
    n = len(bars)
    if n == 0:
        return []
    atr = _get_atr_series(bars, atr_period)
    result: list[float] = [0.0] * n
    if n < donchian_period:
        return result
    # Sliding max/min using O(N) approach over fixed-size window via two passes.
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    from collections import deque

    max_dq: deque[int] = deque()
    min_dq: deque[int] = deque()
    for i in range(n):
        while max_dq and highs[max_dq[-1]] <= highs[i]:
            max_dq.pop()
        max_dq.append(i)
        while min_dq and lows[min_dq[-1]] >= lows[i]:
            min_dq.pop()
        min_dq.append(i)
        while max_dq[0] <= i - donchian_period:
            max_dq.popleft()
        while min_dq[0] <= i - donchian_period:
            min_dq.popleft()
        if i >= donchian_period - 1:
            width = highs[max_dq[0]] - lows[min_dq[0]]
            atr_val = atr[i]
            result[i] = (width / atr_val) if atr_val > 0 else 0.0
    return result


def _get_donchian_width_series(
    bars: list[OhlcvBar], donchian_period: int, atr_period: int
) -> list[float]:
    key = (id(bars), len(bars), donchian_period, atr_period)
    cached = _DONCHIAN_WIDTH_CACHE.get(key)
    if cached is not None:
        return cached
    series = _compute_donchian_width_series(bars, donchian_period, atr_period)
    _DONCHIAN_WIDTH_CACHE[key] = series
    return series


@dataclass(frozen=True)
class ADXGate(RegimeGate):
    """B1: forbid entry when ADX(period) is outside [min_adx, max_adx].

    Below 20 is conventionally "no trend / chop"; above 60 = very late in a
    trend, reversal risk.
    """

    period: int = 14
    min_adx: float = 20.0
    max_adx: float = 60.0
    name: str = "adx_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        # Need 2*period bars of warm-up; admit by default before then.
        if index < self.period * 2:
            return True
        adx_series = _get_adx_series(bars, self.period)
        if index >= len(adx_series):
            return True
        adx = adx_series[index]
        return self.min_adx <= adx <= self.max_adx


@dataclass(frozen=True)
class DonchianWidthGate(RegimeGate):
    """B2: forbid entry when the Donchian channel width / ATR ratio is below
    `width_atr_threshold` — a narrow channel is a chop signature."""

    donchian_period: int = 24  # ~6h on 15m bars
    atr_period: int = 14
    width_atr_threshold: float = 3.0
    name: str = "donchian_width_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        warmup = max(self.donchian_period, self.atr_period)
        if index < warmup:
            return True
        series = _get_donchian_width_series(bars, self.donchian_period, self.atr_period)
        if index >= len(series):
            return True
        ratio = series[index]
        return ratio >= self.width_atr_threshold


@dataclass(frozen=True)
class EquityCurveGate(RegimeGate):
    """B5: forbid entry when the running average R-multiple over the last
    `lookback_trades` closed trades is negative — a soft circuit-breaker that
    sits out during edge decay periods.

    Reads `gate_state["recent_r_multiples"]` (a list maintained by the engine).
    Until `min_trades` have closed, the gate is permissive.
    """

    lookback_trades: int = 20
    min_trades: int = 10
    name: str = "equity_curve_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        recent: list[float] = (gate_state or {}).get("recent_r_multiples") or []
        if len(recent) < self.min_trades:
            return True
        window = recent[-self.lookback_trades :]
        avg_r = sum(window) / len(window)
        return avg_r >= 0.0


@dataclass(frozen=True)
class SessionGate(RegimeGate):
    """D5: forbid entry outside the configured UTC hour band.

    `allowed_utc_hours` is a tuple of integers (0-23). An empty tuple means
    "all hours allowed" — same as NullRegimeGate. Wrap-around bands (e.g.
    22-02) are supported by listing the hours explicitly.
    """

    allowed_utc_hours: tuple[int, ...] = ()
    name: str = "session_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        if not self.allowed_utc_hours:
            return True
        from datetime import UTC

        hour = bars[index].close_time.astimezone(UTC).hour
        return hour in self.allowed_utc_hours


@dataclass(frozen=True)
class VolumeConfirmedGate(RegimeGate):
    """D1: forbid entry unless the current bar's volume exceeds
    `volume_multiplier × MA(period)` of recent volume.

    Default multiplier 1.5× MA(20) on 15m bars is a conservative
    "confirmation candle" signature for breakouts / reclaims.
    """

    period: int = 20
    volume_multiplier: float = 1.5
    name: str = "volume_confirmed_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        if index < self.period:
            return True
        window = bars[index - self.period : index]
        if not window:
            return True
        avg_volume = sum(bar.volume for bar in window) / len(window)
        if avg_volume <= 0:
            return True
        current_volume = bars[index].volume
        return current_volume >= avg_volume * self.volume_multiplier


# Module-level lazy cache for external (cross-asset) bars used by gates.
_EXTERNAL_BARS_CACHE: dict[str, tuple[list[OhlcvBar], dict[Any, int]]] = {}


def _load_external_bars(path: str) -> tuple[list[OhlcvBar], dict[Any, int]]:
    """Lazy-load and cache an external OHLCV CSV (e.g. BTC/JPY) keyed by path.

    Used by cross-asset gates (BtcMomentumGate). Returns (bars,
    index_by_open_time_utc). The index is keyed by UTC `datetime`.
    """
    if path in _EXTERNAL_BARS_CACHE:
        return _EXTERNAL_BARS_CACHE[path]
    from datetime import UTC

    from research.src.adapters.csv_bar_repository import read_bars_from_csv

    bars = read_bars_from_csv(path)
    index = {bar.open_time.astimezone(UTC): i for i, bar in enumerate(bars)}
    _EXTERNAL_BARS_CACHE[path] = (bars, index)
    return bars, index


@dataclass(frozen=True)
class BtcMomentumGate(RegimeGate):
    """Allow entry only when BTC has moved at least ``min_abs_return_pct`` over
    the last ``lookback_bars`` (close-to-close).

    Derived from post-mortem analysis: v0 trades when |BTC 4-bar return| was
    in [0.0%, 0.3%] (sideways) had WR 31-39%; trades when BTC was moving
    clearly (in either direction) had WR 45-46%. Filtering out sideways-BTC
    entries gives the largest OOS edge boost in the post-mortem sweep.

    Loads BTC bars lazily from ``bars_path`` and aligns by UTC open_time.
    Research-only — production deployment would route BTC quotes via a
    proper market-data port.
    """

    bars_path: str = "research/data/raw/btcjpy_15m_1y.csv"
    lookback_bars: int = 4
    min_abs_return_pct: float = 0.3
    name: str = "btc_momentum_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        try:
            btc_bars, btc_index = _load_external_bars(self.bars_path)
        except Exception:
            return True
        from datetime import UTC

        target = bars[index].open_time.astimezone(UTC)
        # Find the BTC bar with the largest open_time <= target
        import bisect

        sorted_keys = sorted(btc_index.keys())
        pos = bisect.bisect_right(sorted_keys, target) - 1
        if pos < 0:
            return True
        btc_idx = btc_index[sorted_keys[pos]]
        if btc_idx < self.lookback_bars:
            return True
        prior_close = btc_bars[btc_idx - self.lookback_bars].close
        current_close = btc_bars[btc_idx].close
        if prior_close <= 0:
            return True
        ret_pct = abs((current_close - prior_close) / prior_close * 100)
        return ret_pct >= self.min_abs_return_pct


_FUNDING_CACHE: dict[str, tuple[list[int], list[float]]] = {}


def _load_funding_series(path: str) -> tuple[list[int], list[float]]:
    """funding CSV(funding_time,funding_rate,..)を (epoch_ms 昇順, rate) で lazy load"""
    if path in _FUNDING_CACHE:
        return _FUNDING_CACHE[path]
    import csv as _csv
    from datetime import datetime as _dt

    times: list[int] = []
    rates: list[float] = []
    with open(path) as f:
        for row in _csv.DictReader(f):
            ts = _dt.strptime(row["funding_time"], "%Y-%m-%dT%H:%M:%SZ")
            times.append(int(ts.replace(tzinfo=UTC_TZ).timestamp() * 1000))
            rates.append(float(row["funding_rate"]))
    _FUNDING_CACHE[path] = (times, rates)
    return times, rates


@dataclass(frozen=True)
class FundingGate(RegimeGate):
    """Track ⑤: 外部取引所(Binance) SOL perp funding を逆張り filter にする

    funding > high_threshold（long 過密）→ LONG をブロック
    funding < low_threshold（short 過密）→ SHORT をブロック
    bar 時刻以前で最新の funding(8h)値を参照（lookahead 無し）
    research 専用 GMO に funding 系列が無いため外部 proxy を CSV で読む
    """

    funding_path: str = "research/data/raw/sol_funding_binance_8h.csv"
    low_threshold: float = -0.0002
    high_threshold: float = 0.0001
    name: str = "funding_gate"

    def _funding_at(self, bar_ms: int) -> float | None:
        import bisect

        times, rates = _load_funding_series(self.funding_path)
        pos = bisect.bisect_right(times, bar_ms) - 1
        if pos < 0:
            return None
        return rates[pos]

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        return True  # 方向別判定は allow_for_direction で行う

    def allow_for_direction(
        self,
        *,
        direction: Any,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        bar_ms = int(bars[index].close_time.astimezone(UTC_TZ).timestamp() * 1000)
        funding = self._funding_at(bar_ms)
        if funding is None:
            return True
        if direction == "LONG" and funding > self.high_threshold:
            return False
        if direction == "SHORT" and funding < self.low_threshold:
            return False
        return True


@dataclass(frozen=True)
class ATRPctRangeGate(RegimeGate):
    """Allow entry only when ATR(period)/close is within [min_atr_pct, max_atr_pct].

    Derived from post-mortem analysis: SOL/JPY v0 trades in the bottom ATR%
    quintile (< 0.36) had 36-37% WR vs 47% in the top quintile. Low-vol
    regimes generate fast stop-outs.
    """

    period: int = 14
    min_atr_pct: float = 0.0
    max_atr_pct: float = 100.0
    name: str = "atr_pct_range_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        if index < self.period:
            return True
        atr_series = _get_atr_series(bars, self.period)
        if index >= len(atr_series):
            return True
        atr_value = atr_series[index]
        close = bars[index].close
        if close <= 0:
            return True
        atr_pct = atr_value / close * 100
        return self.min_atr_pct <= atr_pct <= self.max_atr_pct


@dataclass(frozen=True)
class DirectionalSessionGate(RegimeGate):
    """Direction-aware time-of-day filter.

    Allows LONG entries only during ``long_allowed_utc_hours`` AND SHORT
    entries only during ``short_allowed_utc_hours``. Empty tuple = allow all
    hours for that direction. Derived from post-mortem cross-tab:
    SHORT × deep-night JST 00-06 had WR 52% while SHORT × morning had 27%;
    LONG × evening had WR 32% while LONG × morning had 47%.

    `allow()` (pre-direction) is permissive — returns True if EITHER
    direction has an allowed band at this hour. The direction-specific
    check happens in `allow_for_direction()` once the strategy decides.
    """

    long_allowed_utc_hours: tuple[int, ...] = ()
    short_allowed_utc_hours: tuple[int, ...] = ()
    name: str = "directional_session_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        from datetime import UTC

        hour = bars[index].close_time.astimezone(UTC).hour
        long_ok = (not self.long_allowed_utc_hours) or hour in self.long_allowed_utc_hours
        short_ok = (not self.short_allowed_utc_hours) or hour in self.short_allowed_utc_hours
        return long_ok or short_ok

    def allow_for_direction(
        self,
        *,
        direction: Any,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        from datetime import UTC

        hour = bars[index].close_time.astimezone(UTC).hour
        if direction == "LONG":
            return (not self.long_allowed_utc_hours) or hour in self.long_allowed_utc_hours
        if direction == "SHORT":
            return (not self.short_allowed_utc_hours) or hour in self.short_allowed_utc_hours
        return True


@dataclass(frozen=True)
class CompositeRegimeGate(RegimeGate):
    """Allow entry only if every sub-gate allows it (AND semantics)."""

    gates: tuple[RegimeGate, ...] = ()
    name: str = "composite_gate"

    def allow(
        self,
        *,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        for gate in self.gates:
            if not gate.allow(bars=bars, index=index, config=config, gate_state=gate_state):
                return False
        return True

    def allow_for_direction(
        self,
        *,
        direction: Any,
        bars: list[OhlcvBar],
        index: int,
        config: dict[str, Any],
        gate_state: dict[str, Any] | None = None,
    ) -> bool:
        for gate in self.gates:
            if not gate.allow_for_direction(
                direction=direction,
                bars=bars,
                index=index,
                config=config,
                gate_state=gate_state,
            ):
                return False
        return True


__all__ = [
    "ADXGate",
    "ATRPctRangeGate",
    "BtcMomentumGate",
    "CompositeRegimeGate",
    "DirectionalSessionGate",
    "DonchianWidthGate",
    "EquityCurveGate",
    "FundingGate",
    "NullRegimeGate",
    "SessionGate",
    "VolumeConfirmedGate",
]
