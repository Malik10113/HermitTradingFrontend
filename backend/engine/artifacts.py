"""Run artifact writer.

Every run directory contains the artifacts named by the brief.  Writing is
atomic per file and the manifest records SHA-256 of each artifact after the
files are written.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from backend.config import RUNS_DIR


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str))
    os.replace(tmp, path)


def _write_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema | None = None) -> None:
    if not rows:
        table = pa.table({}) if schema is None else schema.empty_table()
    else:
        table = pa.Table.from_pylist(rows, schema=schema)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    tmp.replace(path)


def write_artifacts(
    run_id: str,
    config: dict[str, Any],
    manifest: dict[str, Any],
    result: dict[str, Any],
    log_text: str = "",
) -> dict[str, Any]:
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "config.json", config)
    summary = dict(result.get("metrics", {}))
    if result.get("in_sample") is not None:
        summary["in_sample"] = result.get("in_sample")
    if result.get("out_of_sample") is not None:
        summary["out_of_sample"] = result.get("out_of_sample")
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "diagnostics.json", result.get("diagnostics", {}))
    _write_parquet(run_dir / "equity.parquet", result.get("equity", []))
    _write_parquet(
        run_dir / "trades.parquet",
        [{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()} for row in result.get("trades", [])],
    )
    _write_parquet(
        run_dir / "positions.parquet",
        [{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()} for row in result.get("positions", [])],
    )
    _write_parquet(
        run_dir / "signals.parquet",
        [{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()} for row in result.get("signals", [])],
    )
    _write_parquet(run_dir / "costs.parquet", [result.get("costs", {})])
    (run_dir / "log.txt").write_text(log_text)

    manifest = dict(manifest)
    manifest["artifacts"] = {}
    for name in (
        "config.json",
        "summary.json",
        "equity.parquet",
        "trades.parquet",
        "positions.parquet",
        "signals.parquet",
        "costs.parquet",
        "diagnostics.json",
        "log.txt",
    ):
        manifest["artifacts"][name] = sha256_file(run_dir / name)
    manifest["phase_breakdown"] = result.get("phase_breakdown", {})
    _write_json(run_dir / "manifest.json", manifest)
    return manifest


def read_artifact(run_id: str, name: str) -> Path | None:
    allowed = {
        "config.json",
        "summary.json",
        "equity.parquet",
        "trades.parquet",
        "positions.parquet",
        "signals.parquet",
        "costs.parquet",
        "diagnostics.json",
        "log.txt",
        "manifest.json",
    }
    if name not in allowed:
        return None
    path = RUNS_DIR / run_id / name
    return path if path.exists() else None


def read_json_artifact(run_id: str, name: str) -> Any:
    path = read_artifact(run_id, name)
    if path is None:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        # A truncated or vanished artifact must not crash a listing request.
        return None


def parquet_rows(run_id: str, name: str, limit: int = 2000) -> list[dict[str, Any]]:
    path = read_artifact(run_id, name)
    if path is None:
        return []
    table = pq.read_table(path)
    # PyArrow's Table.slice fabricates empty rows on a zero-column table (the
    # shape written for an empty artifact), so guard before slicing: a run with
    # no trades must return no trades.
    if table.num_rows == 0 or table.num_columns == 0:
        return []
    return table.slice(0, limit).to_pylist()
