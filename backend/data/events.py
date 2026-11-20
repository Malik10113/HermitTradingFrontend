"""Observed in-play match events for the chart.

Two read-only sources, both observed rather than derived:

* ``data/events/match_events.parquet`` for goals (ESPN in-play events);
* the cached ESPN summary ``keyEvents`` for the halftime wallclock and
  ``commentary`` for awarded corners.

Cards, VAR decisions and missed/saved penalties are not goals and are never
returned.  Both sources are required: when no explicit path is supplied the
registered dataset is looked up and an absent one raises ``MissingDatasetError``
naming the register entry, so "not restored" never reads as "no goals".  A
present match with no observed event yields no row: this module never invents a
nominal halftime, a corner or a team name.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb

from backend import config
from backend.data import sources
from backend.data.lifecycle import _read_summary, extract_event_wallclock
from backend.data.timeutil import as_utc, isoformat_z, parse_iso_utc

# ``goal---header`` / ``goal---volley`` / ``goal---free-kick`` share the prefix.
GOAL_TYPES = frozenset({"goal", "own-goal", "penalty---scored"})
_HALFTIME = "halftime"
_CORNER = "corner-awarded"


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
    # The ESPN display clock ("32'", "90'+2'") is authoritative for the goal
    # minute; the parquet `minute` is zero-based and loses stoppage time.
    clock_by_wallclock = _goal_display_clocks(match_key) if path is None else {}
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT event_type, wallclock_utc, sequence, team, event_text, minute, added_minute
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
    for event_type, wallclock, _sequence, team, event_text, minute, added_minute in rows:
        if not is_goal_type(event_type):
            continue
        when = as_utc(wallclock)
        row: dict[str, Any] = {
            "ts": isoformat_z(when),
            "type": "goal",
            "label": "GOAL",
            "team": team if isinstance(team, str) and team.strip() else None,
            "detail": event_text
            if isinstance(event_text, str) and event_text.strip()
            else None,
            "minute": int(minute) if minute is not None else None,
            "added_minute": int(added_minute) if added_minute is not None else None,
        }
        clock = clock_by_wallclock.get(isoformat_z(when))
        if clock:
            row["clock"] = clock
        out.append((when, row))
    out.sort(key=lambda item: item[0])
    return out


def goals(match_key: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Observed goal rows for a match; an empty list when there are none."""
    return [row for _, row in _goal_rows(match_key, path)]


def _match_present(match_key: str, path: Path | None = None) -> bool:
    """True when ``match_events`` has at least one row for the match.

    Coverage, not goals: a covered match with no observed goal still returns
    ``True`` here.
    """
    source = _events_path(path)
    if not match_key or not source.is_file():
        return False
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT 1 FROM read_parquet(?) WHERE match_key = ? LIMIT 1",
            [str(source), str(match_key)],
        ).fetchall()
    finally:
        con.close()
    return bool(rows)


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


def _summary_payload(
    match_key: str,
    fixture_map: Path | None,
    summary_dirs: Iterable[Path] | None,
) -> dict[str, Any] | None:
    """First readable cached ESPN summary for a match, or ``None``.

    The fixture map is the entry point; the 2026 cache is keyed by the league
    code and the backfill cache by the ESPN competition slug, so both names are
    tried cache first.  A summary that exists but lacks the requested section
    is still a summary: callers distinguish "no summary" (``None``) from "the
    summary has no such event" (empty).
    """
    fixture = _fixture(match_key, fixture_map)
    if fixture is None:
        return None
    league, event_id = fixture
    from backend.data.lifecycle_backfill import ESPN_LEAGUE_SLUGS

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
            if payload is not None:
                return payload
    return None


def _goal_display_clocks(match_key: str) -> dict[str, str]:
    """ESPN ``clock.displayValue`` per goal wallclock, from the cached summary.

    Key events and commentary are both scanned: key events occasionally omit a
    penalty goal that commentary carries.  A missing summary or clock simply
    yields no display value; the caller still has the observed wallclock.
    """
    payload = _summary_payload(match_key, None, None)
    if payload is None:
        return {}
    clocks: dict[str, str] = {}
    for entry in payload.get("keyEvents") or ():
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("type") or {}).get("type")
        wallclock = entry.get("wallclock")
        clock = (entry.get("clock") or {}).get("displayValue")
        if not is_goal_type(kind) or not isinstance(wallclock, str) or not isinstance(clock, str):
            continue
        try:
            clocks[isoformat_z(parse_iso_utc(wallclock))] = clock
        except ValueError:
            continue
    for entry in payload.get("commentary") or ():
        if not isinstance(entry, dict):
            continue
        play = entry.get("play")
        if not isinstance(play, dict):
            continue
        kind = (play.get("type") or {}).get("type")
        wallclock = play.get("wallclock")
        clock = (play.get("clock") or {}).get("displayValue")
        if not is_goal_type(kind) or not isinstance(wallclock, str) or not isinstance(clock, str):
            continue
        try:
            clocks[isoformat_z(parse_iso_utc(wallclock))] = clock
        except ValueError:
            continue
    return clocks


def _corner_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Awarded corners from the summary ``commentary``, ordered by wallclock.

    Only an observed ``play.wallclock`` is used; a corner without one (or
    without a team) is skipped rather than timed from the match clock.
    """
    observed: list[tuple[datetime, dict[str, Any]]] = []
    for entry in payload.get("commentary") or ():
        if not isinstance(entry, dict):
            continue
        play = entry.get("play")
        if not isinstance(play, dict):
            continue
        event_type = play.get("type")
        if not isinstance(event_type, dict) or event_type.get("type") != _CORNER:
            continue
        team = play.get("team")
        name = team.get("displayName") if isinstance(team, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        raw = play.get("wallclock")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            when = parse_iso_utc(raw)
        except ValueError:
            continue
        observed.append((when, {"ts": isoformat_z(when), "team": name}))
    observed.sort(key=lambda item: item[0])
    return [row for _, row in observed]


def corners(
    match_key: str,
    fixture_map: Path | None = None,
    summary_dirs: Iterable[Path] | None = None,
) -> list[dict[str, Any]] | None:
    """Observed corners, ``[]`` when the summary has none, ``None`` when unknown."""
    if not match_key:
        return None
    payload = _summary_payload(match_key, fixture_map, summary_dirs)
    if payload is None:
        return None
    return _corner_rows(payload)


def match_state(
    match_key: str,
    *,
    events_path: Path | None = None,
    fixture_map: Path | None = None,
    summary_dirs: Iterable[Path] | None = None,
) -> dict[str, list[dict[str, Any]] | None]:
    """Live goals and corners, each ``None`` when its source does not cover it.

    Goals are ``None`` when the match key is absent from ``match_events`` and
    ``[]`` when it is covered without a goal.  Corners are ``None`` when no
    cached summary exists and ``[]`` when the summary has no corner.
    """
    covered = bool(match_key) and _match_present(match_key, events_path)
    goal_rows = _goal_rows(match_key, events_path) if covered else []
    return {
        "goals": [row for _, row in goal_rows] if covered else None,
        "corners": corners(match_key, fixture_map, summary_dirs),
    }


def _halftime_row(
    match_key: str,
    fixture_map: Path | None,
    summary_dirs: Iterable[Path] | None,
) -> tuple[datetime, dict[str, Any]] | None:
    payload = _summary_payload(match_key, fixture_map, summary_dirs)
    if payload is None:
        return None
    when = extract_event_wallclock(payload, _HALFTIME)
    if when is None:
        return None
    return when, {
        "ts": isoformat_z(when),
        "type": "halftime",
        "label": "HALF TIME",
        "team": None,
        "detail": None,
    }


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
