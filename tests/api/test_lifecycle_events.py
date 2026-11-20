"""The lifecycle response carries observed events without changing its phase.

Sources are temporary parquet/gzip files; the sibling repository and the
network are never read.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend import config
from backend.api import services
from backend.data import lifecycle

KNOWN = "epl-known-2026-05-19"
WITHOUT_ROW = "epl-goals-only-2026-05-20"

EVENTS_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string()),
        pa.field("event_type", pa.string()),
        pa.field("wallclock_utc", pa.timestamp("us", tz="UTC")),
        pa.field("sequence", pa.int64()),
        pa.field("team", pa.string()),
        pa.field("event_text", pa.string()),
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


@pytest.fixture()
def sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "data"
    var = tmp_path / "var"
    events_path = root / "events/match_events.parquet"
    fixture_map = root / "events/fixture_map.parquet"
    summary_dir = root / "events/espn/summary"
    lifecycle_path = var / "lifecycle/match_lifecycle.parquet"
    for directory in (events_path.parent, summary_dir, lifecycle_path.parent):
        directory.mkdir(parents=True, exist_ok=True)

    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    match_key=KNOWN,
                    event_type="goal",
                    wallclock_utc=utc("2026-05-19T21:08:56Z"),
                    sequence=0,
                    team="AFC Bournemouth",
                    event_text="Goal! 1-0",
                ),
                dict(
                    match_key=KNOWN,
                    event_type="yellow-card",
                    wallclock_utc=utc("2026-05-19T21:10:00Z"),
                    sequence=1,
                    team="AFC Bournemouth",
                    event_text="card",
                ),
                dict(
                    match_key=WITHOUT_ROW,
                    event_type="goal---header",
                    wallclock_utc=utc("2026-05-20T19:30:00Z"),
                    sequence=0,
                    team="Other FC",
                    event_text="Header",
                ),
            ],
            schema=EVENTS_SCHEMA,
        ),
        events_path,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [dict(match_key=KNOWN, league="epl", espn_event_id="111")],
            schema=FIXTURE_SCHEMA,
        ),
        fixture_map,
    )
    with gzip.open(summary_dir / "epl_111.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "keyEvents": [
                    {"type": {"type": "halftime"}, "wallclock": "2026-05-19T21:50:00Z"}
                ],
                "commentary": [
                    {
                        "play": {
                            "type": {"type": "corner-awarded"},
                            "wallclock": "2026-05-19T20:10:00Z",
                            "team": {"displayName": "AFC Bournemouth"},
                        }
                    }
                ],
            },
            handle,
        )
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    match_key=KNOWN,
                    actual_kickoff_utc=utc("2026-05-19T20:00:00Z"),
                    actual_end_utc=utc("2026-05-19T21:55:00Z"),
                    source="espn_keyevents",
                    precision="observed",
                    lifecycle_version="sha256:test",
                )
            ],
            schema=lifecycle.LIFECYCLE_SCHEMA,
        ),
        lifecycle_path,
    )

    monkeypatch.setattr(config, "DATA_ROOT", root)
    monkeypatch.setattr(config, "VAR_DIR", var)
    monkeypatch.setattr(config, "MATCH_EVENTS", events_path)
    monkeypatch.setattr(config, "FIXTURE_MAP", fixture_map)
    monkeypatch.setattr(config, "ESPN_SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(config, "LIFECYCLE_PATH", lifecycle_path)

    instruments = {
        "m-known": {"match_key": KNOWN},
        "m-without-row": {"match_key": WITHOUT_ROW},
        "m-bare": {"match_key": "epl-no-events"},
    }
    monkeypatch.setattr(
        services, "get_instrument", lambda instrument_id: instruments[instrument_id]
    )
    return instruments


def test_known_row_keeps_its_fields_and_adds_events(sources):
    payload = services.lifecycle("m-known")
    assert payload["match_key"] == KNOWN
    assert "phase" not in payload
    assert payload["actual_kickoff_utc"] == utc("2026-05-19T20:00:00Z")
    assert payload["source"] == "espn_keyevents"
    assert [row["type"] for row in payload["events"]] == ["goal", "halftime"]
    assert [row["ts"] for row in payload["events"]] == [
        "2026-05-19T21:08:56Z",
        "2026-05-19T21:50:00Z",
    ]
    assert payload["match_state"]["goals"] == [
        {
            "ts": "2026-05-19T21:08:56Z",
            "type": "goal",
            "label": "GOAL",
            "team": "AFC Bournemouth",
            "detail": "Goal! 1-0",
        }
    ]
    assert payload["match_state"]["corners"] == [
        {"ts": "2026-05-19T20:10:00Z", "team": "AFC Bournemouth"}
    ]


def test_unknown_phase_still_serves_observed_events(sources):
    payload = services.lifecycle("m-without-row")
    assert payload["phase"] == "unknown"
    assert payload["reason"] == "no verified lifecycle row"
    assert [row["type"] for row in payload["events"]] == ["goal"]
    assert payload["events"][0]["team"] == "Other FC"
    assert [row["ts"] for row in payload["match_state"]["goals"]] == [
        "2026-05-20T19:30:00Z"
    ]
    # no cached summary for this match, so corners are unavailable, not zero
    assert payload["match_state"]["corners"] is None


def test_no_observed_events_is_an_empty_list(sources):
    payload = services.lifecycle("m-bare")
    assert payload["phase"] == "unknown"
    assert payload["events"] == []
    assert payload["match_state"] == {"goals": None, "corners": None}
