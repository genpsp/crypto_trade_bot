from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.data.market_dataset import MarketDataset


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _to_utc(parsed)


def _iso(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ConcreteWindow:
    window_id: str
    type: str
    start: datetime
    end: datetime
    role: str = "test"
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "type": self.type,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "role": self.role,
            "metadata": self.metadata or {},
        }

    def slice_bars(self, bars: list[OhlcvBar]) -> list[OhlcvBar]:
        start_utc = _to_utc(self.start)
        end_utc = _to_utc(self.end)
        return [bar for bar in bars if start_utc <= _to_utc(bar.close_time) <= end_utc]


def infer_bar_minutes(close_times: list[datetime]) -> int:
    if len(close_times) < 2:
        raise ValueError("requires at least 2 OHLCV bars")
    deltas: list[int] = []
    for index in range(1, len(close_times)):
        delta_minutes = int((_to_utc(close_times[index]) - _to_utc(close_times[index - 1])).total_seconds() / 60)
        if delta_minutes > 0:
            deltas.append(delta_minutes)
    if not deltas:
        raise ValueError("failed to infer bar interval")
    inferred = Counter(deltas).most_common(1)[0][0]
    if inferred <= 0 or 1440 % inferred != 0:
        raise ValueError(f"unsupported bar interval minutes: {inferred}")
    return inferred


def split_contiguous_segments(close_times: list[datetime], expected_minutes: int) -> list[tuple[int, int]]:
    if not close_times:
        return []
    segments: list[tuple[int, int]] = []
    segment_start = 0
    for index in range(1, len(close_times)):
        delta_minutes = int((_to_utc(close_times[index]) - _to_utc(close_times[index - 1])).total_seconds() / 60)
        if delta_minutes != expected_minutes:
            segments.append((segment_start, index - 1))
            segment_start = index
    segments.append((segment_start, len(close_times) - 1))
    return segments


def build_windows(window_specs: list[dict[str, Any]], dataset: MarketDataset) -> list[ConcreteWindow]:
    windows: list[ConcreteWindow] = []
    for spec_index, spec in enumerate(window_specs):
        window_type = spec.get("type")
        if window_type == "last_n_days":
            days = float(spec["days"])
            end = dataset.end
            start = end - timedelta(days=days)
            windows.append(
                ConcreteWindow(
                    window_id=f"last_{days:g}d",
                    type="last_n_days",
                    start=start,
                    end=end,
                    metadata={"days": days, "spec_index": spec_index},
                )
            )
            continue

        if window_type == "fixed":
            start = parse_utc_datetime(str(spec["start"]))
            end = parse_utc_datetime(str(spec["end"]))
            windows.append(
                ConcreteWindow(
                    window_id=str(spec.get("id", f"fixed_{spec_index}")),
                    type="fixed",
                    start=start,
                    end=end,
                    metadata={"spec_index": spec_index},
                )
            )
            continue

        if window_type == "rolling":
            length_days = float(spec["length_days"])
            step_days = float(spec.get("step_days", length_days))
            cursor = dataset.start
            index = 0
            while cursor + timedelta(days=length_days) <= dataset.end:
                end = cursor + timedelta(days=length_days)
                windows.append(
                    ConcreteWindow(
                        window_id=f"rolling_{spec_index}_{index}",
                        type="rolling",
                        start=cursor,
                        end=end,
                        metadata={
                            "length_days": length_days,
                            "step_days": step_days,
                            "spec_index": spec_index,
                            "index": index,
                        },
                    )
                )
                cursor += timedelta(days=step_days)
                index += 1
            continue

        if window_type == "walk_forward":
            windows.extend(_build_walk_forward_windows(spec=spec, spec_index=spec_index, dataset=dataset))
            continue

        raise ValueError(f"unsupported window type: {window_type}")
    return windows


def _build_walk_forward_windows(
    *,
    spec: dict[str, Any],
    spec_index: int,
    dataset: MarketDataset,
) -> list[ConcreteWindow]:
    close_times = [bar.close_time for bar in dataset.bars]
    bar_minutes = infer_bar_minutes(close_times)
    bars_per_day = int(1440 / bar_minutes)
    train_days = float(spec["train_days"])
    test_days = float(spec["test_days"])
    step_days = float(spec.get("step_days", test_days))
    train_bars = int(round(train_days * bars_per_day))
    test_bars = int(round(test_days * bars_per_day))
    step_bars = int(round(step_days * bars_per_day))
    max_windows = spec.get("max_windows")
    if train_bars <= 0 or test_bars <= 0 or step_bars <= 0:
        raise ValueError("walk_forward bars must be positive")
    window_bars = train_bars + test_bars
    segments = split_contiguous_segments(close_times, bar_minutes)
    windows: list[ConcreteWindow] = []
    produced_windows = 0
    for segment_index, (segment_start, segment_end) in enumerate(segments):
        if segment_end - segment_start + 1 < window_bars:
            continue
        cursor = segment_start
        window_index = 0
        while cursor + window_bars - 1 <= segment_end:
            train_start = cursor
            train_end = cursor + train_bars - 1
            test_start = train_end + 1
            test_end = test_start + test_bars - 1
            pair_id = f"wf{spec_index}_seg{segment_index}_w{window_index}"
            common_metadata = {
                "spec_index": spec_index,
                "segment_index": segment_index,
                "window_index": window_index,
                "train_days": train_days,
                "test_days": test_days,
                "step_days": step_days,
                "bar_minutes": bar_minutes,
            }
            windows.append(
                ConcreteWindow(
                    window_id=f"{pair_id}_train",
                    type="walk_forward",
                    start=dataset.bars[train_start].close_time,
                    end=dataset.bars[train_end].close_time,
                    role="train",
                    metadata=common_metadata,
                )
            )
            windows.append(
                ConcreteWindow(
                    window_id=f"{pair_id}_test",
                    type="walk_forward",
                    start=dataset.bars[test_start].close_time,
                    end=dataset.bars[test_end].close_time,
                    role="test",
                    metadata=common_metadata,
                )
            )
            produced_windows += 1
            if max_windows is not None and produced_windows >= int(max_windows):
                return windows
            cursor += step_bars
            window_index += 1
    return windows
