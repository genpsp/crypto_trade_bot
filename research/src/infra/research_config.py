from __future__ import annotations

import importlib
import json
from pathlib import Path

from pybot.domain.model.types import BotConfig
from pybot.infra.config import schema as schema_module


def load_bot_config(path: str | Path) -> BotConfig:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Config file not found: {source}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config payload must be object: {source}")

    latest_schema = importlib.reload(schema_module)
    return latest_schema.parse_config(raw)
