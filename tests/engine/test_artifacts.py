"""Artifact reading: an empty artifact must stay empty in the API."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from backend.engine import artifacts


def test_empty_parquet_returns_no_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir()
    # This is the shape write_artifacts produces for an empty artifact.
    pq.write_table(pa.table({}), run_dir / "trades.parquet")

    assert artifacts.parquet_rows("run_empty", "trades.parquet", 100_000) == []


def test_non_empty_parquet_still_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run_rows"
    run_dir.mkdir()
    pq.write_table(pa.table({"trade_id": ["T-1", "T-2"], "net": [1.0, -1.0]}), run_dir / "trades.parquet")

    rows = artifacts.parquet_rows("run_rows", "trades.parquet", 1)
    assert rows == [{"trade_id": "T-1", "net": 1.0}]
