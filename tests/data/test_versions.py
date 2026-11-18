"""Data-version hashing is checkout-independent and content-derived."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend import config
from backend.data import versions


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"deterministic artifact bytes")


def test_data_version_does_not_depend_on_the_checkout_path(tmp_path, monkeypatch):
    """The same ``var/`` contents under two roots must hash the same.

    Absolute paths in the fingerprint previously made a copied worktree look
    like different data, so every existing run was reported as orphaned.
    """
    first_root = tmp_path / "checkout-a"
    second_root = tmp_path / "checkout-b"
    first = first_root / "var/lifecycle/match_lifecycle.parquet"
    second = second_root / "var/lifecycle/match_lifecycle.parquet"
    _write(first)
    second.parent.mkdir(parents=True, exist_ok=True)
    # copy2 keeps size and mtime_ns identical; both are part of the fingerprint.
    shutil.copy2(first, second)

    monkeypatch.setattr(config, "VAR_DIR", first_root / "var")
    version_a = versions.compute_version([(first, None)], "fixture-1")

    monkeypatch.setattr(config, "VAR_DIR", second_root / "var")
    version_b = versions.compute_version([(second, None)], "fixture-1")

    assert version_a == version_b


def test_data_version_changes_with_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path / "var")
    path = tmp_path / "var/settlement/settlement.parquet"
    _write(path)
    before = versions.compute_version([(path, None)], "fixture-1")

    replacement = tmp_path / "replacement.parquet"
    replacement.write_bytes(b"different bytes")
    shutil.copyfile(replacement, path)
    after = versions.compute_version([(path, None)], "fixture-1")

    assert before != after
