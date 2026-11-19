"""Observed in-play match events for the chart.

Two read-only sources, both observed rather than derived:

* ``data/events/match_events.parquet`` for goals (ESPN in-play events);
* the cached ESPN summary ``keyEvents`` for the halftime wallclock.

Cards, VAR decisions and missed/saved penalties are not goals and are never
returned.  Both sources are required: when no explicit path is supplied the
registered dataset is looked up and an absent one raises ``MissingDatasetError``
naming the register entry, so "not restored" never reads as "no goals".  A
present match with no observed event yields no row: this module never invents a
nominal halftime or a team name.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb

from backend import config
from backend.data import sources
from backend.data.lifecycle import _read_summary, extract_event_wallclock
from backend.data.timeutil import as_utc, isoformat_z

# ``goal---header`` / ``goal---volley`` / ``goal---free-kick`` share the prefix.
GOAL_TYPES = frozenset({"goal", "own-goal", "penalty---scored"})
_HALFTIME = "halftime"


def is_goal_type(event_type: object) -> bool:
    """True for an ESPN event type that scored a goal.

    ``penalty---saved``, ``penalty---missed``, ``penalty---hit-woodwork``,
    cards, VAR decisions and ``deleted-after-review`` are excluded.
    """
    if not isinstance(event_type, str):
        return False
    normalised = event_type.strip().lower()
    if normalised in GOAL_TYPES:
        return True
    return normalised.startswith("goal---")


def _events_path(path: Path | None) -> Path:
    """Explicit path as given; otherwise the registered source, or a loud error."""
    if path is not None:
        return Path(path)
    return sources.require_file("espn_match_events")


def _goal_rows(match_key: str, path: Path | None = None) -> list[tuple[datetime, dict[str, Any]]]:
    """Goal events with their observed timestamps, ordered by wallclock."""
    source = _events_path(path)
    if not match_key or not source.is_file():
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT event_type, wallclock_utc, sequence, team, event_text
            FROM read_parquet(?)
            WHERE match_key = ?
              AND wallclock_utc IS NOT NULL
            ORDER BY wallclock_utc, sequence
            """,
            [str(source), str(match_key)],
        ).fetchall()
    finally:
        con.close()
    out: list[tuple[datetime, dict[str, Any]]] = []
    for event_type, wallclock, _sequence, team, event_text in rows:
        if not is_goal_type(event_type):
            continue
        when = as_utc(wallclock)
        out.append(
            (
                when,
                {
                    "ts": isoformat_z(when),
                    "type": "goal",
                    "label": "GOAL",
                    "team": team if isinstance(team, str) and team.strip() else None,
                    "detail": event_text
                    if isinstance(event_text, str) and event_text.strip()
                    else None,
                },
            )
        )
    out.sort(key=lambda item: item[0])
    return out


def goals(match_key: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Observed goal rows for a match; an empty list when there are none."""
    return [row for _, row in _goal_rows(match_key, path)]


def _fixture(match_key: str, fixture_map: Path | None) -> tuple[str, str] | None:
    """``(league, espn_event_id)`` for a match, or ``None``."""
    if fixture_map is not None:
        source = Path(fixture_map)
    else:
        source = sources.require_file("espn_fixture_map")
    if not match_key or not source.is_file():
        return None
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT league, espn_event_id
            FROM read_parquet(?)
            WHERE match_key = ?
              AND league IS NOT NULL
              AND espn_event_id IS NOT NULL
            ORDER BY espn_event_id NULLS LAST, league
            LIMIT 1
            """,
            [str(source), str(match_key)],
        ).fetchall()
    except Exception:  # noqa: BLE001 - an unreadable map means no observed halftime
        return None
    finally:
        con.close()
    if not rows:
        return None
    league, event_id = rows[0]
    return str(league), str(event_id)


def _default_summary_dirs() -> list[Path]:
    """Cache first, then the backfill cache written under ``var/``."""
    return [
        Path(config.ESPN_SUMMARY_DIR),
        Path(config.VAR_DIR) / "raw/espn/summary",
    ]


def _halftime_row(
    match_key: str,
    fixture_map: Path | None,
    summary_dirs: Iterable[Path] | None,
) -> tuple[datetime, dict[str, Any]] | None:
    fixture = _fixture(match_key, fixture_map)
    if fixture is None:
        return None
    league, event_id = fixture
    from backend.data.lifecycle_backfill import ESPN_LEAGUE_SLUGS

    # The 2026 cache is keyed by the league code; the backfill cache uses the
    # ESPN competition slug.  Both are tried, cache first.
    names = [league]
    slug = ESPN_LEAGUE_SLUGS.get(league)
    if slug is not None:
        names.append(slug)
    directories = list(summary_dirs) if summary_dirs is not None else _default_summary_dirs()
    seen: set[Path] = set()
    for directory in directories:
        for name in names:
            candidate = Path(directory) / f"{name}_{event_id}.json.gz"
            if candidate in seen:
                continue
            seen.add(candidate)
            payload = _read_summary(candidate)
            if payload is None:
                continue
            when = extract_event_wallclock(payload, _HALFTIME)
            if when is None:
                continue
            return when, {
                "ts": isoformat_z(when),
                "type": "halftime",
                "label": "HALF TIME",
                "team": None,
                "detail": None,
            }
    return None


def halftime(
    match_key: str,
    fixture_map: Path | None = None,
    summary_dirs: Iterable[Path] | None = None,
) -> dict[str, Any] | None:
    """Observed halftime row, or ``None`` when no cache has the wallclock."""
    found = _halftime_row(match_key, fixture_map, summary_dirs)
    return found[1] if found is not None else None


def lifecycle_events(
    match_key: str,
    *,
    events_path: Path | None = None,
    fixture_map: Path | None = None,
    summary_dirs: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    """Halftime plus goals, sorted by observed timestamp; empty when unknown."""
    if not match_key:
        return []
    observed: list[tuple[datetime, dict[str, Any]]] = _goal_rows(match_key, events_path)
    half = _halftime_row(match_key, fixture_map, summary_dirs)
    if half is not None:
        observed.append(half)
    observed.sort(key=lambda item: item[0])
    return [row for _, row in observed]
