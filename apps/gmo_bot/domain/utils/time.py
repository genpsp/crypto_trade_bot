from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from apps.gmo_bot.domain.model.types import Direction, SignalTimeframe

TIMEFRAME_TO_SECONDS: dict[SignalTimeframe, int] = {
    "15m": 15 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
}
# Single source of truth for the gmo_bot JST constant. Other modules MUST
# import this rather than re-defining `timezone(timedelta(hours=9))` locally.
JST = timezone(timedelta(hours=9))


def format_iso_utc(value: datetime) -> str:
    """Format a ``datetime`` as ISO-8601 with a trailing ``Z`` for UTC.

    Centralises the repeated ``isoformat().replace("+00:00", "Z")`` idiom that
    used to live in firestore_repo.py, run_cycle.py, and similar modules.
    Naive datetimes are treated as UTC for safety.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def get_bar_duration_seconds(timeframe: SignalTimeframe) -> int:
    return TIMEFRAME_TO_SECONDS[timeframe]


def get_last_closed_bar_close(now: datetime, timeframe: SignalTimeframe) -> datetime:
    now_utc = now.astimezone(UTC)
    duration = get_bar_duration_seconds(timeframe)
    epoch_seconds = int(now_utc.timestamp())
    close_epoch_seconds = (epoch_seconds // duration) * duration
    return datetime.fromtimestamp(close_epoch_seconds, tz=UTC)


def get_utc_day_range(target: datetime) -> tuple[str, str]:
    target_utc = target.astimezone(UTC)
    day_start = datetime(
        year=target_utc.year,
        month=target_utc.month,
        day=target_utc.day,
        tzinfo=UTC,
    )
    day_end = day_start + timedelta(days=1) - timedelta(milliseconds=1)
    return day_start.isoformat().replace("+00:00", "Z"), day_end.isoformat().replace("+00:00", "Z")


def get_jst_day_range(target: datetime) -> tuple[str, str]:
    target_jst = target.astimezone(JST)
    day_start = datetime(
        year=target_jst.year,
        month=target_jst.month,
        day=target_jst.day,
        tzinfo=JST,
    )
    day_end = day_start + timedelta(days=1) - timedelta(milliseconds=1)
    return day_start.isoformat(), day_end.isoformat()


def build_trade_id(bar_close_time_iso: str, model_id: str, direction: Direction) -> str:
    # §12.6 caveat: the sanitiser collapses ``.`` (and other punctuation) to
    # ``_``, which means hypothetical model_ids like ``foo.bar`` and
    # ``foo_bar`` would produce the same trade_id. Today all real model_ids
    # match ``[A-Za-z0-9_-]+`` so the collision is not reachable; if that
    # invariant is ever relaxed, append a short ``sha1(model_id)[:6]`` suffix
    # *and* migrate existing OPEN trade IDs in Firestore (changing this
    # format mid-flight will orphan in-flight trades).
    safe_model_id = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in model_id)
    side = "LONG" if direction == "LONG" else "SHORT"
    return f"{bar_close_time_iso}_{safe_model_id}_{side}"


def build_run_id(bar_close_time_iso: str, run_at: datetime) -> str:
    safe_bar = bar_close_time_iso.replace(":", "-").replace(".", "-")
    run_epoch_ms = int(run_at.timestamp() * 1000)
    return f"{safe_bar}_{run_epoch_ms}"
