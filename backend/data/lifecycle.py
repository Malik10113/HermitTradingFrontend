"""Observed match lifecycle from cached ESPN ``keyEvents``.

Only two event types are used: ``kickoff`` and ``end-regular-time``.  The
``wallclock`` field is UTC.  A fixture without both observed timestamps is
omitted, which yields ``phase = unknown`` downstream; a nominal duration is
never substituted.

The builder is offline by default: it reads the cached summaries through
``data/events/fixture_map.parquet`` and never calls the network.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pyarrow as pa

from backend import config
from backend.data import sources, versions
from backend.data._parquet import read_metadata, write_table
from backend.data.timeutil import as_utc, parse_iso_utc

PHASES = ("pre_match", "in_play", "post_match", "settled", "unknown")

LIFECYCLE_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string(), nullable=False),
        pa.field("actual_kickoff_utc", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("actual_end_utc", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("precision", pa.string(), nullable=False),
        pa.field("lifecycle_version", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True)
class LifecycleRow:
    match_key: str
    actual_kickoff_utc: datetime
    actual_end_utc: datetime
    source: str = "espn_keyevents"
    precision: str = "observed"

    def as_dict(self, lifecycle_version: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "match_key": self.match_key,
            "actual_kickoff_utc": self.actual_kickoff_utc,
            "actual_end_utc": self.actual_end_utc,
            "source": self.source,
            "precision": self.precision,
        }
        if lifecycle_version is not None:
            data["lifecycle_version"] = lifecycle_version
        return data


def _path(out_path: Path | None) -> Path:
    return Path(out_path) if out_path is not None else Path(config.LIFECYCLE_PATH)


def _extract_wallclocks(payload: object | None) -> tuple[datetime | None, datetime | None]:
    if not isinstance(payload, dict):
        return None, None
    kickoffs: list[datetime] = []
    ends: list[datetime] = []
    for event in payload.get("keyEvents") or ():
        if not isinstance(event, dict):
            continue
        event_type = (event.get("type") or {}).get("type")
        raw = event.get("wallclock")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            observed = parse_iso_utc(raw)
        except ValueError:
            continue
        if event_type == "kickoff":
            kickoffs.append(observed)
        elif event_type == "end-regular-time":
            ends.append(observed)
    kickoff = min(kickoffs) if kickoffs else None
    end = max(ends) if ends else None
    if kickoff is None or end is None or end < kickoff:
        return kickoff, None
    return kickoff, end


def _read_summary(path: Path) -> dict[str, Any] | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _fixtures(fixture_map: Path) -> list[tuple[str, str, str]]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT match_key, league, espn_event_id
            FROM (
                SELECT
                    match_key,
                    league,
                    espn_event_id,
                    row_number() OVER (
                        PARTITION BY match_key
                        ORDER BY espn_event_id NULLS LAST, league
                    ) AS rn
                FROM read_parquet(?)
                WHERE match_key IS NOT NULL
                  AND espn_event_id IS NOT NULL
                  AND league IS NOT NULL
            )
            WHERE rn = 1
            ORDER BY match_key
            """,
            [str(fixture_map)],
        ).fetchall()
    finally:
        con.close()
    return [(str(match_key), str(league), str(event_id)) for match_key, league, event_id in rows]


def build_match_lifecycle(
    *,
    limit: int | None = None,
    backfill: bool = False,
    fixture_map: Path | None = None,
    summary_dir: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Scan cached summaries and write ``var/lifecycle/match_lifecycle.parquet``.

    ``backfill`` is accepted for CLI symmetry but no network access is
    performed in v1; pre-2026 fixtures are simply absent unless a cached
    summary exists.
    """
    fixture_path = Path(fixture_map) if fixture_map is not None else sources.require_file("espn_fixture_map")
    cache_dir = Path(summary_dir) if summary_dir is not None else sources.require_dir("espn_summary_dir")

    fixtures = _fixtures(fixture_path)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        fixtures = fixtures[:limit]

    expected_files: list[Path] = [
        cache_dir / f"{league}_{event_id}.json.gz" for _, league, event_id in fixtures
    ]
    version = versions.lifecycle_version(expected_files)

    rows: list[dict[str, Any]] = []
    missing = 0
    incomplete = 0
    invalid = 0
    for match_key, league, event_id in fixtures:
        path = cache_dir / f"{league}_{event_id}.json.gz"
        payload = _read_summary(path)
        if payload is None:
            missing += 1
            continue
        kickoff, end = _extract_wallclocks(payload)
        if kickoff is None or end is None:
            incomplete += 1
            continue
        if end < kickoff:
            invalid += 1
            continue
        rows.append(
            {
                "match_key": match_key,
                "actual_kickoff_utc": kickoff,
                "actual_end_utc": end,
                "source": "espn_keyevents",
                "precision": "observed",
                "lifecycle_version": version,
            }
        )

    table = pa.Table.from_pylist(rows, schema=LIFECYCLE_SCHEMA)
    destination = _path(out_path)
    write_table(
        table,
        destination,
        {
            "lifecycle_version": version,
            "builder_version": versions.LIFECYCLE_BUILDER_VERSION,
            "source": "espn_keyevents",
            "fixture_map": sources.relative_to_root(fixture_path),
            "summary_dir": sources.relative_to_root(cache_dir),
            "row_count": table.num_rows,
            "offline": "true",
        },
    )
    return {
        "path": destination,
        "rows": table.num_rows,
        "lifecycle_version": version,
        "fixtures": len(fixtures),
        "missing_summary": missing,
        "incomplete": incomplete,
        "invalid": invalid,
        "backfill": "offline_cache_only" if not backfill else "offline_cache_only",
    }


def load_lifecycle(path: Path | None = None) -> dict[str, LifecycleRow]:
    """All verified lifecycle rows, keyed by ``match_key``."""
    destination = _path(path)
    if not destination.exists():
        build_match_lifecycle(out_path=destination)
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT match_key, actual_kickoff_utc, actual_end_utc, source, precision
            FROM read_parquet(?)
            ORDER BY match_key
            """,
            [str(destination)],
        ).fetchall()
    finally:
        con.close()
    out: dict[str, LifecycleRow] = {}
    for match_key, kickoff, end, source, precision in rows:
        out[str(match_key)] = LifecycleRow(
            match_key=str(match_key),
            actual_kickoff_utc=as_utc(kickoff),
            actual_end_utc=as_utc(end),
            source=str(source),
            precision=str(precision),
        )
    return out


def lifecycle_for(match_key: str, path: Path | None = None) -> LifecycleRow | None:
    """Return the verified row or ``None``; unknown is never guessed."""
    return load_lifecycle(path).get(match_key)


def lifecycle_version(path: Path | None = None) -> str:
    return read_metadata(_path(path)).get("lifecycle_version", "")


def _row_parts(row: LifecycleRow | dict[str, Any] | None) -> tuple[datetime, datetime] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        kickoff = row.get("actual_kickoff_utc")
        end = row.get("actual_end_utc")
    else:
        kickoff = row.actual_kickoff_utc
        end = row.actual_end_utc
    if kickoff is None or end is None:
        return None
    return as_utc(kickoff), as_utc(end)


def phase_at(
    row: LifecycleRow | dict[str, Any] | None,
    ts: datetime,
    settlement_ts: datetime | None = None,
) -> str:
    """Phase from observed timestamps only.

    ``settled`` requires a verified settlement timestamp; without one the state
    after full time is ``post_match``.  A missing lifecycle row is ``unknown``
    even if a settlement timestamp exists.
    """
    when = as_utc(ts)
    parts = _row_parts(row)
    if parts is None:
        return "unknown"
    kickoff, end = parts
    if when < kickoff:
        return "pre_match"
    if when < end:
        return "in_play"
    if settlement_ts is not None and when >= as_utc(settlement_ts):
        return "settled"
    return "post_match"


def _minutes(delta_seconds: float) -> float:
    return delta_seconds / 60.0


def time_to_kickoff_minutes(
    row: LifecycleRow | dict[str, Any] | None, ts: datetime
) -> float | None:
    parts = _row_parts(row)
    if parts is None:
        return None
    kickoff, _ = parts
    return _minutes((kickoff - as_utc(ts)).total_seconds())


def time_since_kickoff_minutes(
    row: LifecycleRow | dict[str, Any] | None, ts: datetime
) -> float | None:
    parts = _row_parts(row)
    if parts is None:
        return None
    kickoff, _ = parts
    return _minutes((as_utc(ts) - kickoff).total_seconds())


def time_to_settlement_minutes(
    row: LifecycleRow | dict[str, Any] | None,
    ts: datetime,
    settlement_ts: datetime | None,
) -> float | None:
    if _row_parts(row) is None or settlement_ts is None:
        return None
    return _minutes((as_utc(settlement_ts) - as_utc(ts)).total_seconds())


def lifecycle_rows(rows: Iterable[LifecycleRow]) -> list[dict[str, Any]]:
    """Serialise rows for diagnostics without inventing fields."""
    return [row.as_dict() for row in rows]
