"""Public re-exports of shared config schema primitives.

The actual implementations currently live in
``apps/dex_bot/infra/config/schema.py`` and were imported by gmo_bot using the
underscore-private names, which broke the encapsulation contract. Until those
helpers can be moved into a proper ``shared/`` home (tracked separately as
"5.2 shared/ 化"), this module gives them stable public names so consumers
outside dex_bot can import them without poking at private symbols.
"""

from __future__ import annotations

from apps.dex_bot.infra.config.schema import (  # noqa: F401
    _parse_exit as parse_exit,
    _parse_risk as parse_risk,
    _parse_strategy as parse_strategy,
    _require as require,
)

__all__ = ["parse_exit", "parse_risk", "parse_strategy", "require"]
