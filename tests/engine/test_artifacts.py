"""Artifact reading: an empty artifact must stay empty in the API."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_truncated_json_artifact_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run_broken"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text('{"net_pnl_usd": 1.0, "in_sample": {')

    # A half-written or corrupted artifact is "not available", not a 500.
    assert artifacts.read_json_artifact("run_broken", "summary.json") is None
    assert artifacts.read_json_artifact("run_broken", "missing.json") is None


def test_write_artifacts_replaces_every_file_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "RUNS_DIR", tmp_path)
    replaced: list[str] = []
    real_replace = artifacts.os.replace

    def spy(src, dst):
        replaced.append(Path(dst).name)
        return real_replace(src, dst)

    monkeypatch.setattr(artifacts.os, "replace", spy)

    manifest = artifacts.write_artifacts(
        "run_atomic",
        {"strategy": {"instrument_id": "x-YES.POLYMARKET"}},
        {"strategy_hash": "sha256:strategy"},
        {
            "metrics": {"net_pnl_usd": 0.0},
            "equity": [],
            "trades": [],
            "positions": [],
            "signals": [],
            "costs": {},
            "diagnostics": {},
        },
        log_text="log",
    )

    run_dir = tmp_path / "run_atomic"
    for name in (
        "config.json",
        "summary.json",
        "diagnostics.json",
        "manifest.json",
        "equity.parquet",
        "trades.parquet",
        "positions.parquet",
        "signals.parquet",
        "costs.parquet",
    ):
        assert (run_dir / name).exists(), name
        assert name in replaced, f"{name} was not written through os.replace"
        # Every temp file is moved into place; none is left behind.
        assert not (run_dir / f"{name}.tmp").exists()
    assert sorted(manifest["artifacts"]) == sorted(
        [
            "config.json",
            "summary.json",
            "equity.parquet",
            "trades.parquet",
            "positions.parquet",
            "signals.parquet",
            "costs.parquet",
            "diagnostics.json",
            "log.txt",
        ]
    )
    assert json.loads((run_dir / "summary.json").read_text()) == {"net_pnl_usd": 0.0}
    assert artifacts.read_json_artifact("run_atomic", "manifest.json")["strategy_hash"] == "sha256:strategy"
