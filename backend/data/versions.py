"""Content-derived data versions.

A data version is SHA-256 over the ordered list of
``(relative path, size, mtime_ns, rows, min_ts, max_ts)`` for the datasets a
result depends on, plus a builder version string.  The fingerprint is cached in
``var/data_versions.json`` so repeated requests do not rescan parquet files.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend import config

BUILDER_VERSION = "bars-1.0.0"
# Bump when the fingerprint schema changes so cached versions are recomputed.
# relpath-1: every hashed path component is checkout-relative (``_rel``), so
# a missing dataset now hashes like a present one and existing production
# versions stay valid; do not bump this without a real schema change.
VERSION_SCHEMA = "relpath-1"
LIFECYCLE_BUILDER_VERSION = "lifecycle-1.0.0"
SETTLEMENT_BUILDER_VERSION = "settlement-1.0.0"
INSTRUMENT_INDEX_BUILDER_VERSION = "instruments-1.0.0"

DATA_ROOT = config.DATA_ROOT


@dataclass(frozen=True)
class Fingerprint:
    path: str
    size: int
    mtime_ns: int
    rows: int | None
    min_ts: int | None
    max_ts: int | None

    def as_list(self) -> list[object]:
        return [self.path, self.size, self.mtime_ns, self.rows, self.min_ts, self.max_ts]


def _rel(path: Path) -> str:
    """A stable, checkout-independent path component for the version hash.

    Sources are relative to the data root; generated artifacts (``var/``) are
    relative to the repository root.  Using an absolute path here made the same
    data version differ between two clones or worktrees, which wrongly orphaned
    every existing run after a move.
    """
    resolved = path.resolve()
    for base in (config.DATA_ROOT.resolve(), config.VAR_DIR.resolve(), config.ROOT.resolve()):
        try:
            return str(resolved.relative_to(base))
        except ValueError:
            continue
    return str(resolved)


def fingerprint(path: Path, timestamp_column: str | None = None) -> Fingerprint:
    stat = path.stat()
    rows: int | None = None
    min_ts: int | None = None
    max_ts: int | None = None
    if timestamp_column is not None:
        try:
            import duckdb

            con = duckdb.connect()
            try:
                q = (
                    f"SELECT count(*), min({timestamp_column}), max({timestamp_column}) "
                    f"FROM read_parquet(?)"
                )
                row = con.execute(q, [str(path)]).fetchone()
                rows, min_ts, max_ts = int(row[0]), _to_ns(row[1]), _to_ns(row[2])
            finally:
                con.close()
        except Exception:  # noqa: BLE001 - a missing optional column is not fatal
            rows = None
    return Fingerprint(_rel(path), stat.st_size, stat.st_mtime_ns, rows, min_ts, max_ts)


def _to_ns(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Tape timestamps are unix seconds; values already in ns are > 1e17.
        v = int(value)
        return v if v > 10**17 else v * 1_000_000_000
    if hasattr(value, "timestamp"):
        return int(value.timestamp() * 1_000_000_000)
    return None


def compute_version(
    datasets: Iterable[tuple[Path, str | None]],
    builder_version: str = BUILDER_VERSION,
) -> str:
    h = hashlib.sha256()
    h.update(builder_version.encode())
    for path, ts_col in datasets:
        if not path.exists():
            h.update(f"missing:{_rel(path)}\n".encode())
            continue
        fp = fingerprint(path, ts_col)
        h.update(json.dumps(fp.as_list(), separators=(",", ":")).encode())
    return "sha256:" + h.hexdigest()


def _cache_load() -> dict[str, str]:
    try:
        return json.loads(config.DATA_VERSIONS_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _cache_store(cache: dict[str, str]) -> None:
    path = config.DATA_VERSIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # A process-unique temp file in the same directory: concurrent workers
    # writing the fingerprint cache must never collide on one fixed name.
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(cache, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cached_version(key: str, datasets: list[tuple[Path, str | None]], builder_version: str) -> str:
    """Compute a version, reusing the cache only when every stat is unchanged."""
    cache = _cache_load()
    stamp = []
    for path, _ in datasets:
        try:
            st = path.stat()
            stamp.append([str(path), st.st_size, st.st_mtime_ns])
        except FileNotFoundError:
            stamp.append([str(path), None, None])
    stamp.append(builder_version)
    stamp.append(VERSION_SCHEMA)
    stamp_key = key + ":" + hashlib.sha256(json.dumps(stamp).encode()).hexdigest()[:16]
    existing = cache.get(stamp_key)
    if existing:
        return existing
    version = compute_version(datasets, builder_version)
    cache[stamp_key] = version
    _cache_store(cache)
    return version


def tape_data_version() -> str:
    from backend.config import EPL_TAPE, FOOTBALL_TAPE, MARKETS

    return cached_version(
        "tape",
        [
            (EPL_TAPE, "timestamp"),
            (FOOTBALL_TAPE, "timestamp"),
            (MARKETS, None),
        ],
        BUILDER_VERSION,
    )


def run_data_version() -> str:
    """Version covering every source a backtest can depend on."""
    from backend.config import EPL_TAPE, FIXTURE_MAP, FOOTBALL_TAPE, LIFECYCLE_PATH, MARKETS, POLYMARKET_MARKETS, SETTLEMENT_PATH

    datasets: list[tuple[Path, str | None]] = [
        (EPL_TAPE, "timestamp"),
        (FOOTBALL_TAPE, "timestamp"),
        (MARKETS, None),
        (POLYMARKET_MARKETS, None),
        (FIXTURE_MAP, None),
        (LIFECYCLE_PATH, None),
        (SETTLEMENT_PATH, None),
    ]
    return cached_version("run", datasets, BUILDER_VERSION)


def instrument_index_version() -> str:
    """Version of the instrument index inputs (tapes, metadata, results)."""
    datasets: list[tuple[Path, str | None]] = [
        (config.EPL_TAPE, "timestamp"),
        (config.FOOTBALL_TAPE, "timestamp"),
        (config.MARKETS, None),
        (config.POLYMARKET_MARKETS, None),
        (config.POLYMARKET_EVENTS, None),
    ]
    return cached_version("instruments", datasets, INSTRUMENT_INDEX_BUILDER_VERSION)


def settlement_version() -> str:
    """Version of the indexed settlement inputs."""
    datasets: list[tuple[Path, str | None]] = [
        (config.POLYMARKET_MARKETS, None),
        (config.MARKETS, None),
    ]
    return cached_version("settlement", datasets, SETTLEMENT_BUILDER_VERSION)


def lifecycle_version(
    summary_files: Iterable[Path] = (),
    *,
    extra_inputs: Iterable[Path] = (),
    extra_tokens: Iterable[str] = (),
) -> str:
    """Version of the lifecycle inputs: fixture map plus cached summaries.

    Callers pass the exact summary files they read so the version changes when a
    cached ESPN payload changes.  ``extra_inputs`` covers the backfill spine and
    caches, and ``extra_tokens`` carries a stable row-content token for a
    backfill run.  ``load_lifecycle`` does not recompute this; the builder
    stores it on the artifact.
    """
    datasets: list[tuple[Path, str | None]] = [(config.FIXTURE_MAP, None)]
    datasets.extend((Path(path), None) for path in sorted(summary_files))
    datasets.extend((Path(path), None) for path in sorted(extra_inputs))
    tokens = list(extra_tokens)
    if not tokens:
        return cached_version("lifecycle", datasets, LIFECYCLE_BUILDER_VERSION)

    import hashlib

    h = hashlib.sha256()
    h.update(LIFECYCLE_BUILDER_VERSION.encode())
    for path, ts_col in datasets:
        if not path.exists():
            h.update(f"missing:{_rel(path)}\n".encode())
            continue
        fp = fingerprint(path, ts_col)
        h.update(json.dumps(fp.as_list(), separators=(",", ":")).encode())
    for token in tokens:
        h.update(token.encode())
    return "sha256:" + h.hexdigest()
