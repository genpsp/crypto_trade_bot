from __future__ import annotations

import json
from pathlib import Path

from pybot.domain.model.types import BotConfig
from pybot.infra.config.schema import parse_config


def load_bot_config(path: str | Path) -> BotConfig:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Config file not found: {source}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    return parse_config(raw)
