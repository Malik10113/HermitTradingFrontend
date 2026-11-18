"""Small, shared Parquet helpers for the data layer.

All generated artifacts are written atomically (temp file + ``os.replace``) and
carry their provenance in the Parquet schema metadata, so a cached artifact can
be validated against the data version that produced it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow as pa
import pyarrow.parquet as pq


def write_table(
    table: pa.Table,
    path: Path,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``table`` atomically with JSON-encoded schema metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {str(k): str(v) for k, v in (metadata or {}).items()}
    if clean:
        existing = dict(table.schema.metadata or {})
        existing.update({k.encode(): v.encode() for k, v in clean.items()})
        table = table.replace_schema_metadata(existing)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    pq.write_table(table, tmp, compression="snappy")
    os.replace(tmp, path)
    return path


def read_metadata(path: Path) -> dict[str, str]:
    """Read schema metadata from a Parquet file, empty when absent."""
    path = Path(path)
    if not path.exists():
        return {}
    schema = pq.read_schema(path)
    meta = schema.metadata or {}
    return {k.decode(): v.decode() for k, v in meta.items()}


def write_metadata_file(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write a deterministic JSON sidecar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True))
    os.replace(tmp, path)
    return path


def rows_to_table(rows: Iterable[Mapping[str, Any]], schema: pa.Schema) -> pa.Table:
    """Build a table from row dicts, enforcing the declared schema."""
    columns: dict[str, list[Any]] = {name: [] for name in schema.names}
    for row in rows:
        for name in schema.names:
            columns[name].append(row.get(name))
    return pa.table(columns, schema=schema)
