from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORED = {"__init__"}


def find_missing_guard_integrations() -> list[str]:
    engine_text = (ROOT / "research/src/domain/backtest_engine.py").read_text(encoding="utf-8")
    missing: list[str] = []
    for risk_dir in sorted((ROOT / "apps").glob("*/domain/risk")):
        for path in sorted(risk_dir.glob("*.py")):
            module = path.stem
            if module in IGNORED:
                continue
            if module not in engine_text:
                missing.append(str(path.relative_to(ROOT)))
    return missing


def main() -> None:
    missing = find_missing_guard_integrations()
    if missing:
        raise SystemExit("Risk guard files not referenced by research backtest engine: " + ", ".join(missing))
    print("[research] guard consistency OK")


if __name__ == "__main__":
    main()
