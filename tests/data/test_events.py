"""Offline tests for the observed in-play events reader.

Every input is a temporary parquet or gzipped summary; the sibling repository
and the network are never read.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.data import events

MATCH = "epl-aaa-bbb-2026-05-19"
OTHER = "epl-ccc-ddd-2026-05-20"
CARD_ONLY = "epl-eee-fff-2026-05-21"

EVENTS_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string()),
        pa.field("league", pa.string()),
        pa.field("event_type", pa.string()),
        pa.field("wallclock_utc", pa.timestamp("us", tz="UTC")),
        pa.field("sequence", pa.int64()),
        pa.field("team", pa.string()),
        pa.field("event_text", pa.string()),
        pa.field("minute", pa.int64()),
        pa.field("added_minute", pa.int64()),
    ]
)

FIXTURE_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string()),
        pa.field("league", pa.string()),
        pa.field("espn_event_id", pa.string()),
    ]
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _event(
    match_key,
    event_type,
    wallclock,
    sequence,
    team=None,
    text=None,
    minute=None,
    added_minute=None,
):
    return dict(
        match_key=match_key,
        league="epl",
        event_type=event_type,
        wallclock_utc=utc(wallclock),
        sequence=sequence,
        team=team,
        event_text=text,
        minute=minute,
        added_minute=added_minute,
    )


def _write_events(path: Path) -> Path:
    rows = [
        _event(MATCH, "yellow-card", "2026-05-19T20:15:00Z", 0, "AFC Bournemouth", "card"),
        _event(
            MATCH,
            "goal",
            "2026-05-19T21:08:56Z",
            1,
            "AFC Bournemouth",
            "Goal! 1-0",
            minute=32,
            added_minute=0,
        ),
        _event(MATCH, "goal---header", "2026-05-19T21:20:00Z", 2, "AFC Bournemouth", "Header"),
        _event(MATCH, "goal---volley", "2026-05-19T21:25:00Z", 3, "AFC Bournemouth", "Volley"),
        _event(MATCH, "goal---free-kick", "2026-05-19T21:30:00Z", 4, "AFC Bournemouth", "Free kick"),
        _event(MATCH, "own-goal", "2026-05-19T21:35:00Z", 5, "AFC Bournemouth", "Own goal"),
        _event(MATCH, "penalty---scored", "2026-05-19T21:40:00Z", 6, "AFC Bournemouth", "Penalty"),
        _event(MATCH, "penalty---saved", "2026-05-19T21:41:00Z", 7, "AFC Bournemouth", "Saved"),
        _event(MATCH, "penalty---missed", "2026-05-19T21:42:00Z", 8, "AFC Bournemouth", "Missed"),
        _event(MATCH, "penalty---hit-woodwork", "2026-05-19T21:43:00Z", 9, "AFC Bournemouth", "Woodwork"),
        _event(
            MATCH,
            "var---referee-decision-cancelled",
            "2026-05-19T21:44:00Z",
            10,
            "AFC Bournemouth",
            "VAR",
        ),
        _event(MATCH, "red-card", "2026-05-19T21:45:00Z", 11, "AFC Bournemouth", "Red"),
        _event(MATCH, "deleted-after-review", "2026-05-19T21:46:00Z", 12, "AFC Bournemouth", "Deleted"),
        # a goal without an observed wallclock is not returned
        dict(
            match_key=MATCH,
            league="epl",
            event_type="goal",
            wallclock_utc=None,
            sequence=13,
            team="AFC Bournemouth",
            event_text="No clock",
        ),
        # another match never leaks into this one
        _event(OTHER, "goal", "2026-05-20T19:00:00Z", 0, "Other FC", "Goal! 1-0"),
        # covered by the source, but with no goal at all
        _event(CARD_ONLY, "yellow-card", "2026-05-21T19:00:00Z", 0, "Card FC", "card"),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA), path)
    return path


def _write_fixture_map(path: Path, rows=None) -> Path:
    if rows is None:
        rows = [dict(match_key=MATCH, league="epl", espn_event_id="111")]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=FIXTURE_SCHEMA), path)
    return path


def _write_summary(path: Path, key_events) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"keyEvents": key_events}, handle)
    return path


def _write_summary_payload(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _corner(wallclock, team):
    return {
        "play": {
            "type": {"type": "corner-awarded"},
            "wallclock": wallclock,
            "team": {"displayName": team},
        }
    }


@pytest.fixture()
def events_path(tmp_path: Path) -> Path:
    return _write_events(tmp_path / "events/match_events.parquet")


def test_goal_type_filter_is_explicit():
    for included in (
        "goal",
        "goal---header",
        "goal---volley",
        "goal---free-kick",
        "own-goal",
        "penalty---scored",
    ):
        assert events.is_goal_type(included) is True, included
    for excluded in (
        "penalty---saved",
        "penalty---missed",
        "penalty---hit-woodwork",
        "var---referee-decision-cancelled",
        "var---referee-decision-confirmed",
        "yellow-card",
        "red-card",
        "deleted-after-review",
        None,
        "",
    ):
        assert events.is_goal_type(excluded) is False, excluded


def test_goals_filters_sorts_and_carries_fields(events_path):
    rows = events.goals(MATCH, events_path)
    assert [row["ts"] for row in rows] == [
        "2026-05-19T21:08:56Z",
        "2026-05-19T21:20:00Z",
        "2026-05-19T21:25:00Z",
        "2026-05-19T21:30:00Z",
        "2026-05-19T21:35:00Z",
        "2026-05-19T21:40:00Z",
    ]
    assert all(row["type"] == "goal" for row in rows)
    assert all(row["label"] == "GOAL" for row in rows)
    assert rows[0] == {
        "ts": "2026-05-19T21:08:56Z",
        "type": "goal",
        "label": "GOAL",
        "team": "AFC Bournemouth",
        "detail": "Goal! 1-0",
        "minute": 32,
        "added_minute": 0,
    }
    assert events.goals(OTHER, events_path)[0]["team"] == "Other FC"


def test_goals_for_unknown_match_and_missing_file(tmp_path):
    assert events.goals("no-such-match", tmp_path / "absent.parquet") == []
    assert events.goals("no-such-match", _write_events(tmp_path / "events.parquet")) == []


def test_goals_requires_the_registered_events_source(tmp_path, monkeypatch):
    from backend import config
    from backend.data import sources

    monkeypatch.setattr(config, "MATCH_EVENTS", tmp_path / "absent/match_events.parquet")
    with pytest.raises(sources.MissingDatasetError) as excinfo:
        events.goals(MATCH)
    assert excinfo.value.dataset == "espn_match_events"
    assert "espn_match_events" in str(excinfo.value)


def test_halftime_requires_the_registered_fixture_map(tmp_path, monkeypatch):
    from backend import config
    from backend.data import sources

    monkeypatch.setattr(config, "FIXTURE_MAP", tmp_path / "absent/fixture_map.parquet")
    with pytest.raises(sources.MissingDatasetError) as excinfo:
        events.halftime(MATCH)
    assert excinfo.value.dataset == "espn_fixture_map"
    assert "espn_fixture_map" in str(excinfo.value)


def test_halftime_from_the_2026_cache(tmp_path, events_path):
    summary_dir = tmp_path / "data/events/espn/summary"
    _write_summary(
        summary_dir / "epl_111.json.gz",
        [
            {"type": {"type": "kickoff"}, "wallclock": "2026-05-19T20:00:00Z"},
            {"type": {"type": "goal"}, "wallclock": "2026-05-19T20:30:00Z"},
            {"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"},
        ],
    )
    fixture_map = _write_fixture_map(tmp_path / "events/fixture_map.parquet")

    row = events.halftime(MATCH, fixture_map, [summary_dir])
    assert row == {
        "ts": "2026-05-19T21:50:00Z",
        "type": "halftime",
        "label": "HALF TIME",
        "team": None,
        "detail": None,
    }


def test_halftime_uses_the_earliest_observed_wallclock(tmp_path):
    summary_dir = tmp_path / "summary"
    _write_summary(
        summary_dir / "epl_111.json.gz",
        [
            {"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:55:00Z"},
            {"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"},
        ],
    )
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    row = events.halftime(MATCH, fixture_map, [summary_dir])
    assert row is not None and row["ts"] == "2026-05-19T21:50:00Z"


def test_halftime_from_the_backfill_cache_uses_the_espn_slug(tmp_path):
    backfill_dir = tmp_path / "var/raw/espn/summary"
    _write_summary(
        backfill_dir / "eng.1_222.json.gz",
        [{"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"}],
    )
    fixture_map = _write_fixture_map(
        tmp_path / "fixture_map.parquet",
        [dict(match_key=MATCH, league="epl", espn_event_id="222")],
    )
    row = events.halftime(MATCH, fixture_map, [tmp_path / "cache", backfill_dir])
    assert row is not None and row["type"] == "halftime"


def test_halftime_missing_summary_or_fixture_is_none(tmp_path):
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    # fixture exists, no cached summary
    assert events.halftime(MATCH, fixture_map, [tmp_path / "empty"]) is None
    # summary exists, but the match is not in the fixture map
    summary_dir = tmp_path / "summary"
    _write_summary(
        summary_dir / "epl_111.json.gz",
        [{"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"}],
    )
    assert events.halftime("not-in-the-map", fixture_map, [summary_dir]) is None
    # summary without a halftime event
    _write_summary(
        summary_dir / "epl_222.json.gz",
        [{"type": {"type": "kickoff"}, "wallclock": "2026-05-19T20:00:00Z"}],
    )
    other_map = _write_fixture_map(
        tmp_path / "other_map.parquet",
        [dict(match_key="other", league="epl", espn_event_id="222")],
    )
    assert events.halftime("other", other_map, [summary_dir]) is None


def test_lifecycle_events_merges_halftime_and_goals(tmp_path, events_path):
    summary_dir = tmp_path / "summary"
    _write_summary(
        summary_dir / "epl_111.json.gz",
        [{"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"}],
    )
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")

    rows = events.lifecycle_events(
        MATCH,
        events_path=events_path,
        fixture_map=fixture_map,
        summary_dirs=[summary_dir],
    )
    assert [row["ts"] for row in rows] == [
        "2026-05-19T21:08:56Z",
        "2026-05-19T21:20:00Z",
        "2026-05-19T21:25:00Z",
        "2026-05-19T21:30:00Z",
        "2026-05-19T21:35:00Z",
        "2026-05-19T21:40:00Z",
        "2026-05-19T21:50:00Z",
    ]
    assert rows[-1]["type"] == "halftime"
    assert rows[-1]["label"] == "HALF TIME"


def test_lifecycle_events_unknown_match_is_empty(tmp_path):
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    empty_events = tmp_path / "events.parquet"
    pq.write_table(
        pa.Table.from_pylist([], schema=EVENTS_SCHEMA),
        empty_events,
    )
    assert (
        events.lifecycle_events(
            "never-seen",
            events_path=empty_events,
            fixture_map=fixture_map,
            summary_dirs=[tmp_path / "no-summaries"],
        )
        == []
    )


def test_corners_parse_commentary_with_team_and_observed_time(tmp_path):
    summary_dir = tmp_path / "summary"
    _write_summary_payload(
        summary_dir / "epl_111.json.gz",
        {
            "keyEvents": [],
            "commentary": [
                _corner("2026-05-19T20:10:00Z", "AFC Bournemouth"),
                {
                    "play": {
                        "type": {"type": "goal"},
                        "wallclock": "2026-05-19T20:20:00Z",
                        "team": {"displayName": "AFC Bournemouth"},
                    }
                },
                _corner("2026-05-19T20:05:00Z", "Chelsea"),
                # a corner without an observed wallclock is skipped
                {"play": {"type": {"type": "corner-awarded"}, "team": {"displayName": "Chelsea"}}},
                # and one without a team cannot fill the required field
                {"play": {"type": {"type": "corner-awarded"}, "wallclock": "2026-05-19T20:30:00Z"}},
                {"note": "no play object"},
            ],
        },
    )
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")

    assert events.corners(MATCH, fixture_map, [summary_dir]) == [
        {"ts": "2026-05-19T20:05:00Z", "team": "Chelsea"},
        {"ts": "2026-05-19T20:10:00Z", "team": "AFC Bournemouth"},
    ]


def test_corners_from_the_backfill_cache_uses_the_espn_slug(tmp_path):
    backfill_dir = tmp_path / "var/raw/espn/summary"
    _write_summary_payload(
        backfill_dir / "eng.1_222.json.gz",
        {"commentary": [_corner("2026-05-19T20:10:00Z", "Chelsea")]},
    )
    fixture_map = _write_fixture_map(
        tmp_path / "fixture_map.parquet",
        [dict(match_key=MATCH, league="epl", espn_event_id="222")],
    )
    assert events.corners(MATCH, fixture_map, [tmp_path / "cache", backfill_dir]) == [
        {"ts": "2026-05-19T20:10:00Z", "team": "Chelsea"}
    ]


def test_corners_summary_without_corners_is_empty(tmp_path):
    summary_dir = tmp_path / "summary"
    _write_summary(
        summary_dir / "epl_111.json.gz",
        [{"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"}],
    )
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    assert events.corners(MATCH, fixture_map, [summary_dir]) == []


def test_corners_without_fixture_or_summary_is_none(tmp_path):
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    # no match key at all never reaches the sources
    assert events.corners("") is None
    # fixture exists, no cached summary
    assert events.corners(MATCH, fixture_map, [tmp_path / "empty"]) is None
    # summary exists, but the match is not in the fixture map
    summary_dir = tmp_path / "summary"
    _write_summary_payload(
        summary_dir / "epl_111.json.gz",
        {"commentary": [_corner("2026-05-19T20:10:00Z", "Chelsea")]},
    )
    assert events.corners("not-in-the-map", fixture_map, [summary_dir]) is None


def test_match_state_goals_covered_empty_and_absent(tmp_path, events_path):
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    missing_corners = [tmp_path / "no-summaries"]

    state = events.match_state(
        MATCH, events_path=events_path, fixture_map=fixture_map, summary_dirs=missing_corners
    )
    assert [row["ts"] for row in state["goals"]] == [
        "2026-05-19T21:08:56Z",
        "2026-05-19T21:20:00Z",
        "2026-05-19T21:25:00Z",
        "2026-05-19T21:30:00Z",
        "2026-05-19T21:35:00Z",
        "2026-05-19T21:40:00Z",
    ]
    assert state["corners"] is None

    # covered by the source, no goal observed
    assert (
        events.match_state(
            CARD_ONLY, events_path=events_path, fixture_map=fixture_map, summary_dirs=missing_corners
        )["goals"]
        == []
    )
    # absent from the source
    assert (
        events.match_state(
            "never-seen", events_path=events_path, fixture_map=fixture_map, summary_dirs=missing_corners
        )["goals"]
        is None
    )
    assert (
        events.match_state(
            "never-seen",
            events_path=tmp_path / "absent.parquet",
            fixture_map=fixture_map,
            summary_dirs=missing_corners,
        )["goals"]
        is None
    )


def test_match_state_corners_present_empty_and_absent(tmp_path, events_path):
    fixture_map = _write_fixture_map(tmp_path / "fixture_map.parquet")
    summary_dir = tmp_path / "summary"
    _write_summary_payload(
        summary_dir / "epl_111.json.gz",
        {"commentary": [_corner("2026-05-19T20:10:00Z", "AFC Bournemouth")]},
    )
    state = events.match_state(
        MATCH, events_path=events_path, fixture_map=fixture_map, summary_dirs=[summary_dir]
    )
    assert state["corners"] == [{"ts": "2026-05-19T20:10:00Z", "team": "AFC Bournemouth"}]
    assert len(state["goals"]) == 6

    # the summary exists but has no corner
    _write_summary(summary_dir / "epl_111.json.gz", [])
    assert (
        events.match_state(
            MATCH, events_path=events_path, fixture_map=fixture_map, summary_dirs=[summary_dir]
        )["corners"]
        == []
    )

    # no summary at all
    assert (
        events.match_state(
            MATCH,
            events_path=events_path,
            fixture_map=fixture_map,
            summary_dirs=[tmp_path / "empty"],
        )["corners"]
        is None
    )
