from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
import os
from pathlib import Path
import tempfile
from math import isfinite
from typing import Literal

mpl_config_dir = Path(tempfile.gettempdir()) / "crypto_trade_bot_matplotlib"
mpl_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

BalanceUnit = Literal["JPY", "USDC"]


@dataclass(frozen=True)
class BalanceChartSeries:
    label: str
    unit: BalanceUnit
    points: list[tuple[str, float]]


def render_balance_chart_png(
    *,
    title: str,
    series: list[BalanceChartSeries],
    target_date_jst: str,
) -> bytes | None:
    normalized = [_normalize_series(item) for item in series]
    normalized = [item for item in normalized if item["points"]]
    if not normalized:
        return None

    target_date = date.fromisoformat(target_date_jst)
    start_date = target_date - timedelta(days=29)
    end_date = target_date
    unit = _resolve_unit(normalized)

    fig, ax = plt.subplots(figsize=(12, 6), dpi=100)
    try:
        for index, item in enumerate(normalized):
            color = _series_color(str(item["label"]), str(item["unit"]), index)
            first_segment = True
            for segment in _split_contiguous_segments(item["points"]):
                dates = [point[0] for point in segment]
                values = [point[1] for point in segment]
                ax.plot(
                    dates,
                    values,
                    marker="o",
                    linestyle="-" if len(segment) >= 2 else "None",
                    linewidth=2,
                    markersize=4,
                    color=color,
                    label=str(item["label"]) if first_segment else "_nolegend_",
                )
                first_segment = False

        ax.set_title(title or "Balance trend (last 30 days, JST)")
        ax.set_xlabel("Date (JST)")
        ax.set_ylabel(f"Balance ({unit})")
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
        ax.legend(loc="best")
        ax.set_xlim(start_date, end_date)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.yaxis.set_major_formatter(FuncFormatter(_value_formatter(unit)))
        fig.autofmt_xdate(rotation=0)
        fig.tight_layout()

        output = BytesIO()
        fig.savefig(output, format="png", dpi=100)
        return output.getvalue()
    finally:
        plt.close(fig)


def _normalize_series(item: BalanceChartSeries) -> dict[str, object]:
    points: list[tuple[date, float]] = []
    for raw_date, raw_value in item.points:
        try:
            parsed_date = date.fromisoformat(raw_date)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not isfinite(value):
            continue
        points.append((parsed_date, value))
    points.sort(key=lambda point: point[0])
    return {"label": item.label, "unit": item.unit, "points": points}


def _resolve_unit(series: list[dict[str, object]]) -> BalanceUnit:
    for item in series:
        unit = item.get("unit")
        if unit == "JPY":
            return "JPY"
        if unit == "USDC":
            return "USDC"
    return "USDC"


def _split_contiguous_segments(points: object) -> list[list[tuple[date, float]]]:
    if not isinstance(points, list):
        return []
    segments: list[list[tuple[date, float]]] = []
    current: list[tuple[date, float]] = []
    previous_date: date | None = None
    for point in points:
        if not isinstance(point, tuple) or len(point) != 2:
            continue
        point_date, value = point
        if not isinstance(point_date, date) or not isinstance(value, float):
            continue
        if previous_date is not None and (point_date - previous_date).days > 1:
            if current:
                segments.append(current)
            current = []
        current.append((point_date, value))
        previous_date = point_date
    if current:
        segments.append(current)
    return segments


def _series_color(label: str, unit: str, index: int) -> str:
    normalized_label = label.lower()
    if unit == "JPY" or "gmo" in normalized_label:
        return "#1f77b4"
    if unit == "USDC" or "dex" in normalized_label:
        return "#2ca02c"
    palette = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b"]
    return palette[index % len(palette)]


def _value_formatter(unit: BalanceUnit):
    if unit == "JPY":
        return lambda value, _pos: f"{value:,.0f}"
    return lambda value, _pos: f"{value:,.2f}"
