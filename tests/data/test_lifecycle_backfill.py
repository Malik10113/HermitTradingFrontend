"""Offline tests for the ESPN lifecycle backfill.

Everything here uses synthetic payloads and a fake fetcher.  The only HTTP
layer test passes a mocked response object to ``fetch_json``; the network is
never contacted.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend import config
from backend.data import lifecycle, lifecycle_backfill
from backend.data._parquet import write_table


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


EVENTS_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string()),
        pa.field("league", pa.string()),
        pa.field("event_date", pa.date32()),
        pa.field("home_name", pa.string()),
        pa.field("away_name", pa.string()),
        pa.field("home_code", pa.string()),
        pa.field("away_code", pa.string()),
        pa.field("home_norm", pa.string()),
        pa.field("away_norm", pa.string()),
        pa.field("match_id", pa.string()),
    ]
)

FIXTURE_MAP_SCHEMA = pa.schema(
    [
        pa.field("match_key", pa.string()),
        pa.field("league", pa.string()),
        pa.field("match_id", pa.string()),
        pa.field("espn_event_id", pa.string()),
        pa.field("orientation", pa.string()),
        pa.field("kickoff_utc", pa.timestamp("us", tz="UTC")),
        pa.field("result_kickoff_utc", pa.timestamp("us", tz="UTC")),
        pa.field("result_date", pa.date32()),
    ]
)


@dataclass
class Env:
    root: Path
    var: Path
    events: Path
    fixture_map: Path
    source_summary: Path
    lifecycle: Path
    scoreboard: Path
    backfill_summary: Path


def _write_events(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA), path)


def _write_fixture_map(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=FIXTURE_MAP_SCHEMA), path)


def _write_summary(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _event_row(
    match_key: str,
    league: str,
    event_date: date,
    home_norm: str,
    away_norm: str,
    home_code: str,
    away_code: str,
) -> dict[str, Any]:
    return {
        "match_key": match_key,
        "league": league,
        "event_date": event_date,
        "home_name": home_norm.title(),
        "away_name": away_norm.title(),
        "home_code": home_code,
        "away_code": away_code,
        "home_norm": home_norm,
        "away_norm": away_norm,
        "match_id": f"m-{match_key}",
    }


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    root = tmp_path / "data"
    var = tmp_path / "var"
    events = root / "results/polymarket_events.parquet"
    fixture_map = root / "events/fixture_map.parquet"
    source_summary = root / "events/espn/summary"
    lifecycle_path = var / "lifecycle/match_lifecycle.parquet"
    scoreboard = var / "raw/espn/scoreboard"
    backfill_summary = var / "raw/espn/summary"

    source_summary.mkdir(parents=True, exist_ok=True)
    _write_events(events, [])
    _write_fixture_map(fixture_map, [])

    monkeypatch.setattr(config, "DATA_ROOT", root)
    monkeypatch.setattr(config, "VAR_DIR", var)
    monkeypatch.setattr(config, "POLYMARKET_EVENTS", events)
    monkeypatch.setattr(config, "FIXTURE_MAP", fixture_map)
    monkeypatch.setattr(config, "ESPN_SUMMARY_DIR", source_summary)
    monkeypatch.setattr(config, "ESPN_SCOREBOARD_DIR", scoreboard)
    monkeypatch.setattr(config, "LIFECYCLE_PATH", lifecycle_path)
    monkeypatch.setattr(config, "BARS_DIR", var / "bars")
    monkeypatch.setattr(config, "CATALOG_DIR", var / "catalog")
    monkeypatch.setattr(config, "SETTLEMENT_PATH", var / "settlement/settlement.parquet")
    monkeypatch.setattr(config, "INDEX_PATH", var / "instruments/instruments.parquet")
    monkeypatch.setattr(config, "RUNS_DIR", var / "runs")
    monkeypatch.setattr(config, "LOGS_DIR", var / "logs")
    monkeypatch.setattr(config, "APP_DB", var / "app.db")
    monkeypatch.setattr(config, "DATA_VERSIONS_PATH", var / "data_versions.json")

    return Env(
        root=root,
        var=var,
        events=events,
        fixture_map=fixture_map,
        source_summary=source_summary,
        lifecycle=lifecycle_path,
        scoreboard=scoreboard,
        backfill_summary=backfill_summary,
    )


def _scoreboard(date_iso: str, *events: tuple[str, str, str, str, str]) -> dict[str, Any]:
    return {
        "events": [
            {
                "id": event_id,
                "date": date_iso,
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"displayName": home, "abbreviation": home_code},
                            },
                            {
                                "homeAway": "away",
                                "team": {"displayName": away, "abbreviation": away_code},
                            },
                        ]
                    }
                ],
            }
            for event_id, home, away, home_code, away_code in events
        ]
    }


def _summary(kickoff: str | None, end: str | None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if kickoff is not None:
        events.append({"type": {"type": "kickoff"}, "wallclock": kickoff})
    if end is not None:
        events.append({"type": {"type": "end-regular-time"}, "wallclock": end})
    return {"keyEvents": events}


class FakeEspn:
    def __init__(
        self,
        scoreboards: dict[tuple[str, str], dict[str, Any]] | None = None,
        summaries: dict[tuple[str, str], dict[str, Any]] | None = None,
        *,
        fetch_delay_s: float = 0.0,
    ) -> None:
        self.scoreboards = scoreboards or {}
        self.summaries = summaries or {}
        self.fetch_delay_s = fetch_delay_s
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if self.fetch_delay_s:
            time.sleep(self.fetch_delay_s)
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        parts = parsed.path.split("/")
        league = parts[parts.index("soccer") + 1]
        query = parse_qs(parsed.query)
        if "dates" in query:
            key = (league, query["dates"][0])
            if key not in self.scoreboards:
                raise lifecycle_backfill.HttpError(url, 404)
            return json.loads(json.dumps(self.scoreboards[key]))
        key = (league, query["event"][0])
        if key not in self.summaries:
            raise lifecycle_backfill.HttpError(url, 404)
        return json.loads(json.dumps(self.summaries[key]))


class FailingFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError(f"unexpected network fetch: {url}")


def _run(env: Env, fetcher: Any, **kwargs: Any) -> lifecycle_backfill.BackfillResult:
    return lifecycle_backfill.run_backfill(
        events_path=env.events,
        scoreboard_dir=env.scoreboard,
        summary_dir=env.backfill_summary,
        source_summary_dir=env.source_summary,
        delay_s=0.0,
        fetcher=fetcher,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 18),
        **kwargs,
    )


def test_backfill_matches_names_and_codes_and_extracts_wallclocks(env: Env) -> None:
    _write_events(
        env.events,
        [
            _event_row(
                "epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30),
                "burnley", "newcastle united", "bur", "new",
            ),
            # Names deliberately do not match; the ESPN abbreviations do.
            _event_row(
                "epl-ccc-ddd-2025-12-30", "epl", date(2025, 12, 30),
                "home alias", "away alias", "ars", "ast",
            ),
            _event_row(
                "epl-eee-fff-2025-12-30", "epl", date(2025, 12, 30),
                "no such club", "other club", "zzz", "qqq",
            ),
        ],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
                ("9002", "Arsenal", "Aston Villa", "ARS", "AST"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:12Z", "2025-12-30T21:20:40Z"
            ),
            ("eng.1", "9002"): _summary(
                "2025-12-30T20:00:00Z", "2025-12-30T21:50:00Z"
            ),
        },
    )

    result = _run(env, fetcher)

    assert result.report.dates_scanned == 1
    assert result.report.requests == 3  # one scoreboard + two summaries
    assert result.report.matched == 2
    assert result.report.unmatched == 1
    assert result.report.incomplete == 0
    assert result.report.rows_written == 2
    assert len(result.rows) == 2
    by_key = {row["match_key"]: row for row in result.rows}
    assert by_key["epl-aaa-bbb-2025-12-30"]["actual_kickoff_utc"] == utc(
        "2025-12-30T19:30:12Z"
    )
    assert by_key["epl-aaa-bbb-2025-12-30"]["actual_end_utc"] == utc(
        "2025-12-30T21:20:40Z"
    )
    assert by_key["epl-ccc-ddd-2025-12-30"]["source"] == "espn_keyevents"
    assert by_key["epl-ccc-ddd-2025-12-30"]["precision"] == "observed"
    # One scoreboard request for the shared date.
    assert sum("/scoreboard?" in call for call in fetcher.calls) == 1
    assert (env.scoreboard / "eng.1_2025-12-30.json.gz").exists()
    assert (env.backfill_summary / "eng.1_9001.json.gz").exists()


def test_missing_wallclock_is_incomplete_and_not_written(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={("eng.1", "9001"): _summary("2025-12-30T19:30:00Z", None)},
    )

    result = _run(env, fetcher)

    assert result.report.matched == 1
    assert result.report.incomplete == 1
    assert result.report.rows_written == 0
    assert result.rows == []


def test_unmatched_fixture_is_skipped(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "no such", "other", "zzz", "qqq")],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        }
    )

    result = _run(env, fetcher)

    assert result.report.matched == 0
    assert result.report.unmatched == 1
    assert result.report.rows_written == 0
    assert result.rows == []
    assert sum("/summary?" in call for call in fetcher.calls) == 0


def test_second_run_reuses_cache_and_makes_no_requests(env: Env) -> None:
    _write_events(
        env.events,
        [
            _event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
            _event_row("epl-ccc-ddd-2025-12-30", "epl", date(2025, 12, 30), "arsenal", "aston villa", "ars", "ast"),
        ],
    )
    scoreboard = _scoreboard(
        "2025-12-30T19:30Z",
        ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
        ("9002", "Arsenal", "Aston Villa", "ARS", "AST"),
    )
    summaries = {
        ("eng.1", "9001"): _summary("2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"),
        ("eng.1", "9002"): _summary("2025-12-30T20:00:00Z", "2025-12-30T21:50:00Z"),
    }
    first = _run(
        env,
        FakeEspn(scoreboards={("eng.1", "20251230"): scoreboard}, summaries=summaries),
    )
    assert first.report.requests == 3

    failing = FailingFetcher()
    second = _run(env, failing)

    assert failing.calls == 0
    assert second.report.requests == 0
    assert second.report.cache_hits == 3
    assert second.rows == first.rows


def test_refresh_ignores_cached_responses(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    scoreboard = _scoreboard(
        "2025-12-30T19:30Z",
        ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
    )
    first = _run(
        env,
        FakeEspn(
            scoreboards={("eng.1", "20251230"): scoreboard},
            summaries={
                ("eng.1", "9001"): _summary(
                    "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
                )
            },
        ),
    )
    assert first.rows[0]["actual_end_utc"] == utc("2025-12-30T21:20:00Z")

    refreshed_fetcher = FakeEspn(
        scoreboards={("eng.1", "20251230"): scoreboard},
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:35:00Z", "2025-12-30T21:25:00Z"
            )
        },
    )
    refreshed = _run(env, refreshed_fetcher, refresh=True)

    assert refreshed.report.requests == 2
    assert refreshed.report.cache_hits == 0
    assert refreshed.rows[0]["actual_kickoff_utc"] == utc("2025-12-30T19:35:00Z")
    assert refreshed.rows[0]["actual_end_utc"] == utc("2025-12-30T21:25:00Z")


def test_cached_2026_summary_wins_over_backfill(env: Env) -> None:
    _write_events(
        env.events,
        [
            _event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
            _event_row("epl-ccc-ddd-2025-12-30", "epl", date(2025, 12, 30), "arsenal", "aston villa", "ars", "ast"),
        ],
    )
    _write_fixture_map(
        env.fixture_map,
        [
            {
                "match_key": "epl-aaa-bbb-2025-12-30",
                "league": "epl",
                "match_id": "m1",
                "espn_event_id": "111",
                "orientation": "normal",
                "kickoff_utc": utc("2025-12-30T18:00:00Z"),
                "result_kickoff_utc": utc("2025-12-30T18:00:00Z"),
                "result_date": date(2025, 12, 30),
            }
        ],
    )
    _write_summary(
        env.source_summary,
        "epl_111",
        _summary("2025-12-30T18:00:00Z", "2025-12-30T19:50:00Z"),
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
                ("9002", "Arsenal", "Aston Villa", "ARS", "AST"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary("2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"),
            ("eng.1", "9002"): _summary("2025-12-30T20:00:00Z", "2025-12-30T21:50:00Z"),
        },
    )

    result = lifecycle.build_match_lifecycle(
        backfill=True,
        fetcher=fetcher,
        delay_s=0.0,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 18),
    )

    assert result["rows"] == 2
    assert result["preserved_existing"] == 0
    assert result["backfill_report"]["skipped"] == 1
    rows = lifecycle.load_lifecycle()
    assert rows["epl-aaa-bbb-2025-12-30"].actual_kickoff_utc == utc(
        "2025-12-30T18:00:00Z"
    )
    assert rows["epl-aaa-bbb-2025-12-30"].actual_end_utc == utc(
        "2025-12-30T19:50:00Z"
    )
    assert rows["epl-ccc-ddd-2025-12-30"].actual_kickoff_utc == utc(
        "2025-12-30T20:00:00Z"
    )
    assert result["built_on"] == "2026-11-18"


def test_existing_artifact_row_is_preserved(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    existing = pa.Table.from_pylist(
        [
            {
                "match_key": "epl-aaa-bbb-2025-12-30",
                "actual_kickoff_utc": utc("2025-12-30T18:00:00Z"),
                "actual_end_utc": utc("2025-12-30T19:50:00Z"),
                "source": "espn_keyevents",
                "precision": "observed",
                "lifecycle_version": "sha256:prior",
            }
        ],
        schema=lifecycle.LIFECYCLE_SCHEMA,
    )
    write_table(existing, env.lifecycle, {})
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
    )

    result = lifecycle.build_match_lifecycle(
        backfill=True,
        fetcher=fetcher,
        delay_s=0.0,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 18),
    )

    assert result["rows"] == 1
    assert result["preserved_existing"] == 1
    assert result["backfill_report"]["skipped"] == 1
    assert result["backfill_report"]["rows_written"] == 0
    row = lifecycle.load_lifecycle()["epl-aaa-bbb-2025-12-30"]
    assert row.actual_kickoff_utc == utc("2025-12-30T18:00:00Z")
    assert row.actual_end_utc == utc("2025-12-30T19:50:00Z")


def test_no_network_when_backfill_is_disabled(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    monkeypatch.setattr(
        lifecycle_backfill,
        "fetch_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    result = lifecycle.build_match_lifecycle(backfill=False)

    assert result["backfill"] == "offline_cache_only"
    assert result["backfill_report"] is None
    assert result["rows"] == 0


def test_cli_no_backfill_stays_offline(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        lifecycle_backfill,
        "fetch_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    path = Path(config.ROOT) / "scripts" / "build_match_lifecycle.py"
    spec = importlib.util.spec_from_file_location("htf_build_match_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    code = module.main(["--no-backfill", "--out", str(env.lifecycle)])

    assert code == 0
    output = capsys.readouterr().out
    assert "lifecycle_version=sha256:" in output
    assert "backfill " not in output


def test_backfill_rerun_is_idempotent_and_version_is_stable(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
    )
    first = lifecycle.build_match_lifecycle(
        backfill=True,
        fetcher=fetcher,
        delay_s=0.0,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 18),
    )
    failing = FailingFetcher()
    second = lifecycle.build_match_lifecycle(
        backfill=True,
        fetcher=failing,
        delay_s=0.0,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 18),
    )

    assert failing.calls == 0
    assert second["rows"] == first["rows"] == 1
    assert second["lifecycle_version"] == first["lifecycle_version"]
    assert second["backfill_report"]["requests"] == 0
    assert second["preserved_existing"] == 1


def test_leagues_filter_selects_only_requested_league(env: Env) -> None:
    _write_events(
        env.events,
        [
            _event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
            _event_row("lal-aaa-bbb-2025-12-30", "lal", date(2025, 12, 30), "real madrid", "sevilla", "rea", "sev"),
        ],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
    )

    result = _run(env, fetcher, leagues=["epl"])

    assert result.report.leagues == ["epl"]
    assert result.report.fixtures == 1
    assert len(result.rows) == 1
    assert all("eng.1" in call for call in fetcher.calls)


class FakeClock:
    """Thread-safe fake monotonic clock advanced by fake sleeps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now = 0.0

    def clock(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += max(0.0, seconds)


def test_concurrency_produces_same_rows_and_version(env: Env) -> None:
    fixtures = [
        ("epl-aaa-bbb-2025-12-30", "burnley", "newcastle united", "bur", "new"),
        ("epl-ccc-ddd-2025-12-30", "arsenal", "aston villa", "ars", "ast"),
        ("epl-eee-fff-2025-12-30", "chelsea", "bournemouth", "che", "bou"),
        ("epl-ggg-hhh-2025-12-30", "everton", "fulham", "eve", "ful"),
    ]
    _write_events(
        env.events,
        [
            _event_row(key, "epl", date(2025, 12, 30), home, away, hc, ac)
            for key, home, away, hc, ac in fixtures
        ],
    )
    events = {
        "burnley": ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
        "arsenal": ("9002", "Arsenal", "Aston Villa", "ARS", "AST"),
        "chelsea": ("9003", "Chelsea", "Bournemouth", "CHE", "BOU"),
        "everton": ("9004", "Everton", "Fulham", "EVE", "FUL"),
    }
    scoreboard = _scoreboard(
        "2025-12-30T19:30Z",
        *((event_id, home, away, hc, ac) for event_id, home, away, hc, ac in events.values()),
    )
    summaries = {
        ("eng.1", event_id): _summary(
            f"2025-12-30T{kickoff}:00Z", f"2025-12-30T{end}:00Z"
        )
        for (event_id, *_rest), (kickoff, end) in zip(
            events.values(),
            (("19:30", "21:20"), ("20:00", "21:50"), ("20:30", "22:20"), ("21:00", "22:50")),
        )
    }

    def fetcher() -> FakeEspn:
        return FakeEspn(
            scoreboards={("eng.1", "20251230"): scoreboard},
            summaries=summaries,
        )

    serial_path = env.var / "lifecycle/serial.parquet"
    serial = lifecycle.build_match_lifecycle(
        backfill=True,
        out_path=serial_path,
        fetcher=fetcher(),
        delay_s=0.0,
        concurrency=1,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 19),
    )
    shutil.rmtree(env.scoreboard, ignore_errors=True)
    shutil.rmtree(env.backfill_summary, ignore_errors=True)
    parallel_path = env.var / "lifecycle/parallel.parquet"
    parallel = lifecycle.build_match_lifecycle(
        backfill=True,
        out_path=parallel_path,
        fetcher=fetcher(),
        delay_s=0.0,
        concurrency=4,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 19),
    )

    assert serial["rows"] == parallel["rows"] == 4
    assert serial["lifecycle_version"] == parallel["lifecycle_version"]
    assert serial["backfill_report"]["requests"] == parallel["backfill_report"]["requests"] == 5
    assert lifecycle.load_lifecycle(serial_path) == lifecycle.load_lifecycle(parallel_path)


def test_global_rate_limit_is_not_multiplied_by_workers() -> None:
    clock = FakeClock()
    requester = lifecycle_backfill._Requester(
        lambda _url: {},
        delay_s=0.25,
        sleep=clock.sleep,
        clock=clock.clock,
    )
    urls = [f"https://example.test/{index}" for index in range(6)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(requester.get, urls))

    starts = sorted(requester.start_times)
    assert requester.request_count == 6
    assert len(starts) == 6
    assert all(round(later - earlier, 9) >= 0.25 for earlier, later in zip(starts, starts[1:]))


def test_duplicate_summary_url_is_coalesced_and_written_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_events(
        env.events,
        [
            _event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
            # A second Polymarket leg for the same ESPN event (same teams/date).
            _event_row("epl-aaa-bbb-2025-12-30-b", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
        ],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
        fetch_delay_s=0.02,
    )

    writes: list[Path] = []
    write_lock = threading.Lock()
    original_write = lifecycle_backfill._write_gzip_json

    def tracking_write(path: Path, payload: dict[str, Any]) -> Path:
        with write_lock:
            writes.append(Path(path))
        return original_write(path, payload)

    monkeypatch.setattr(lifecycle_backfill, "_write_gzip_json", tracking_write)

    result = _run(env, fetcher, concurrency=4)

    summary_path = env.backfill_summary / "eng.1_9001.json.gz"
    assert len(result.rows) == 2
    assert result.report.matched == 2
    assert result.report.requests == 2  # one scoreboard + one summary
    assert result.report.coalesced == 1
    assert sum(1 for call in fetcher.calls if "/summary?" in call) == 1
    assert writes.count(summary_path) == 1


def test_one_failed_summary_does_not_abort_others(env: Env) -> None:
    _write_events(
        env.events,
        [
            _event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new"),
            _event_row("epl-ccc-ddd-2025-12-30", "epl", date(2025, 12, 30), "arsenal", "aston villa", "ars", "ast"),
        ],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
                ("9002", "Arsenal", "Aston Villa", "ARS", "AST"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
    )

    result = _run(env, fetcher, concurrency=4)

    assert len(result.rows) == 1
    assert result.rows[0]["match_key"] == "epl-aaa-bbb-2025-12-30"
    assert result.report.matched == 2
    assert result.report.rows_written == 1
    assert len(result.report.errors) == 1
    assert "event 9002" in result.report.errors[0]


def test_concurrency_validation_rejects_out_of_range(env: Env) -> None:
    for value in (0, -1, 9):
        with pytest.raises(ValueError):
            _run(env, FailingFetcher(), concurrency=value)
        with pytest.raises(ValueError):
            lifecycle.build_match_lifecycle(concurrency=value)


def test_stage_writes_next_then_replaces(env: Env) -> None:
    _write_events(
        env.events,
        [_event_row("epl-aaa-bbb-2025-12-30", "epl", date(2025, 12, 30), "burnley", "newcastle united", "bur", "new")],
    )
    fetcher = FakeEspn(
        scoreboards={
            ("eng.1", "20251230"): _scoreboard(
                "2025-12-30T19:30Z",
                ("9001", "Burnley", "Newcastle United", "BUR", "NEW"),
            )
        },
        summaries={
            ("eng.1", "9001"): _summary(
                "2025-12-30T19:30:00Z", "2025-12-30T21:20:00Z"
            )
        },
    )

    result = lifecycle.build_match_lifecycle(
        backfill=True,
        stage=True,
        fetcher=fetcher,
        delay_s=0.0,
        sleep=lambda _seconds: None,
        today=date(2026, 11, 19),
    )

    assert result["staged"] is True
    assert env.lifecycle.exists()
    assert not (env.lifecycle.with_name(env.lifecycle.name + ".next")).exists()
    assert lifecycle.load_lifecycle()["epl-aaa-bbb-2025-12-30"].actual_end_utc == utc(
        "2025-12-30T21:20:00Z"
    )


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


def test_fetch_json_retries_transient_429_then_succeeds() -> None:
    sleeps: list[float] = []
    responses = [_Response(429, b""), _Response(200, b'{"ok": true}')]
    calls: list[str] = []

    def opener(request: Any, timeout: float) -> _Response:
        calls.append(request.full_url)
        return responses.pop(0)

    payload = lifecycle_backfill.fetch_json(
        "https://example.test/api",
        opener=opener,
        sleep=sleeps.append,
        max_retries=2,
        retry_backoff_s=0.01,
    )

    assert payload == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [0.01]


def test_fetch_json_raises_after_bounded_5xx_retries() -> None:
    sleeps: list[float] = []
    responses = [_Response(503, b""), _Response(500, b""), _Response(502, b"")]

    def opener(request: Any, timeout: float) -> _Response:
        return responses.pop(0)

    with pytest.raises(lifecycle_backfill.HttpError) as excinfo:
        lifecycle_backfill.fetch_json(
            "https://example.test/api",
            opener=opener,
            sleep=sleeps.append,
            max_retries=2,
            retry_backoff_s=0.01,
        )

    assert excinfo.value.status == 502
    assert sleeps == [0.01, 0.02]


def test_fetch_json_does_not_retry_404() -> None:
    sleeps: list[float] = []

    def opener(request: Any, timeout: float) -> _Response:
        return _Response(404, b"")

    with pytest.raises(lifecycle_backfill.HttpError) as excinfo:
        lifecycle_backfill.fetch_json(
            "https://example.test/api",
            opener=opener,
            sleep=sleeps.append,
            max_retries=3,
            retry_backoff_s=0.01,
        )

    assert excinfo.value.status == 404
    assert sleeps == []


def test_fetch_json_rejects_invalid_json() -> None:
    def opener(request: Any, timeout: float) -> _Response:
        return _Response(200, b"not json")

    with pytest.raises(lifecycle_backfill.HttpError):
        lifecycle_backfill.fetch_json(
            "https://example.test/api",
            opener=opener,
            sleep=lambda _seconds: None,
        )
