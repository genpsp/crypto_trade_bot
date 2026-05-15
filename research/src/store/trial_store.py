from __future__ import annotations

from pathlib import Path
import json
import shutil
from typing import Any


def _require_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as error:
        raise RuntimeError(
            "Run store requires pyarrow. Install dependencies with `pip install -r requirements.txt`."
        ) from error
    return pa, pq


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _maybe_json_load(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        return _json_load(value)
    return value


def flatten_trial_row(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    flattened: dict[str, Any] = {
        "trial_id": row.get("trial_id"),
        "model_id": row.get("model_id"),
        "config": _json_dump(row.get("config", {})),
        "dataset_key": _json_dump(row.get("dataset_key", {})),
        "window": _json_dump(row.get("window", {})),
        "tags": _json_dump(row.get("tags", {})),
        "no_signal_reason_counts": _json_dump(row.get("no_signal_reason_counts", {})),
        "runtime_seconds": float(row.get("runtime_seconds") or 0.0),
        "error": row.get("error"),
    }
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            flattened[f"summary_{key}"] = _json_dump(value)
        else:
            flattened[f"summary_{key}"] = value
    return flattened


def unflatten_trial_row(row: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    restored: dict[str, Any] = {}
    for key, value in row.items():
        if key.startswith("summary_"):
            summary_key = key.removeprefix("summary_")
            summary[summary_key] = _maybe_json_load(value)
        elif key in {"config", "dataset_key", "window", "tags", "no_signal_reason_counts"}:
            restored[key] = _json_load(value)
        else:
            restored[key] = value
    restored["summary"] = summary
    return restored


def flatten_trade_row(trial_id: str, trade_index: int, trade: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {"trial_id": trial_id, "trade_index": int(trade_index)}
    for key, value in trade.items():
        if isinstance(value, (dict, list)):
            flattened[key] = _json_dump(value)
        else:
            flattened[key] = value
    return flattened


def unflatten_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _maybe_json_load(value)
        for key, value in row.items()
        if key not in {"trial_id", "trade_index"}
    }


def _validate_trial_id_for_path(trial_id: str) -> str:
    if not trial_id or trial_id in {".", ".."} or "/" in trial_id or "\\" in trial_id:
        raise ValueError(f"unsafe trial_id for trade path: {trial_id!r}")
    return trial_id


def _empty_trade_table(pa: Any) -> Any:
    return pa.table(
        {
            "trial_id": pa.array([], type=pa.string()),
            "trade_index": pa.array([], type=pa.int64()),
        }
    )


class TrialStore:
    def __init__(self, runs_root: str | Path = "research/data/runs"):
        self.runs_root = Path(runs_root)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def trade_path(self, run_id: str, trial_id: str) -> Path:
        safe_trial_id = _validate_trial_id_for_path(str(trial_id))
        return self.run_dir(run_id) / "trades" / f"{safe_trial_id}.parquet"

    def latest_run_id(self) -> str:
        if not self.runs_root.exists():
            raise FileNotFoundError(f"runs root does not exist: {self.runs_root}")
        candidates = sorted(path.name for path in self.runs_root.iterdir() if path.is_dir())
        if not candidates:
            raise FileNotFoundError(f"no runs found in: {self.runs_root}")
        return candidates[-1]

    def resolve_run_id(self, run_id: str) -> str:
        return self.latest_run_id() if run_id == "latest" else run_id

    def write_run(
        self,
        *,
        run_id: str,
        manifest: dict[str, Any],
        rows: list[dict[str, Any]],
        legacy_output: str | Path | None = None,
        trades_by_trial_id: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Path:
        pa, pq = _require_pyarrow()
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        flat_rows = [flatten_trial_row(row) for row in rows]
        table = pa.Table.from_pylist(flat_rows)
        pq.write_table(table, run_dir / "trials.parquet", compression="zstd")

        if trades_by_trial_id is not None:
            trades_dir = run_dir / "trades"
            trades_dir.mkdir(parents=True, exist_ok=True)
            for trial_id, trades in sorted(trades_by_trial_id.items()):
                flat_trades = [
                    flatten_trade_row(str(trial_id), index, trade)
                    for index, trade in enumerate(trades)
                ]
                trade_table = pa.Table.from_pylist(flat_trades) if flat_trades else _empty_trade_table(pa)
                pq.write_table(trade_table, self.trade_path(run_id, str(trial_id)), compression="zstd")

        if legacy_output is not None:
            legacy_payload = {**manifest, "trials": rows}
            legacy_path = Path(legacy_output)
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(legacy_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return run_dir

    def load_manifest(self, run_id: str) -> dict[str, Any]:
        resolved_run_id = self.resolve_run_id(run_id)
        path = self.run_dir(resolved_run_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"manifest not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"manifest must be object: {path}")
        return payload

    def load_trials(self, run_id: str) -> list[dict[str, Any]]:
        _, pq = _require_pyarrow()
        resolved_run_id = self.resolve_run_id(run_id)
        path = self.run_dir(resolved_run_id) / "trials.parquet"
        if not path.exists():
            raise FileNotFoundError(f"trials parquet not found: {path}")
        return [unflatten_trial_row(dict(row)) for row in pq.read_table(path).to_pylist()]

    def load_trades(self, run_id: str, trial_id: str) -> list[dict[str, Any]]:
        _, pq = _require_pyarrow()
        resolved_run_id = self.resolve_run_id(run_id)
        path = self.trade_path(resolved_run_id, trial_id)
        if not path.exists():
            raise FileNotFoundError(f"trades parquet not found: {path}")
        rows = [dict(row) for row in pq.read_table(path).to_pylist()]
        rows.sort(key=lambda row: int(row.get("trade_index") or 0))
        return [unflatten_trade_row(row) for row in rows]

    def copy_notebook_placeholder(self, run_id: str, source: str | Path, name: str | None = None) -> None:
        target_dir = self.run_dir(run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        source_path = Path(source)
        shutil.copy2(source_path, target_dir / (name or source_path.name))
