from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from apps.dex_bot.domain.model.types import BotConfig
from research.src.data.market_dataset import DatasetKey
from research.src.eval.window import ConcreteWindow


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def make_trial_id(
    *,
    model_id: str,
    config: BotConfig | dict[str, Any],
    dataset_key: DatasetKey,
    window: ConcreteWindow,
    tags: dict[str, Any] | None = None,
) -> str:
    payload = {
        "model_id": model_id,
        "config": config,
        "dataset_key": dataset_key.to_dict(),
        "window": window.to_dict(),
        "tags": tags or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TrialSpec:
    trial_id: str
    model_id: str
    config: BotConfig
    dataset_key: DatasetKey
    window: ConcreteWindow
    tags: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        config: BotConfig,
        dataset_key: DatasetKey,
        window: ConcreteWindow,
        tags: dict[str, Any] | None = None,
    ) -> "TrialSpec":
        resolved_tags = tags or {}
        return cls(
            trial_id=make_trial_id(
                model_id=model_id,
                config=config,
                dataset_key=dataset_key,
                window=window,
                tags=resolved_tags,
            ),
            model_id=model_id,
            config=config,
            dataset_key=dataset_key,
            window=window,
            tags=resolved_tags,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "model_id": self.model_id,
            "config": self.config,
            "dataset_key": self.dataset_key.to_dict(),
            "window": self.window.to_dict(),
            "tags": self.tags,
        }


@dataclass
class TrialResult:
    trial_id: str
    summary: dict[str, Any]
    no_signal_reason_counts: dict[str, int]
    runtime_seconds: float
    error: str | None = None
    trades: list[dict[str, Any]] | None = None

    def to_dict(self, *, include_trades: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trial_id": self.trial_id,
            "summary": self.summary,
            "no_signal_reason_counts": self.no_signal_reason_counts,
            "runtime_seconds": self.runtime_seconds,
            "error": self.error,
        }
        if include_trades and self.trades is not None:
            payload["trades"] = self.trades
        return payload
