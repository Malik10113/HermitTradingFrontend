from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.data import lifecycle
from backend.data.timeutil import NaiveTimestampError


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_build_parses_kickoff_and_end_from_keyevents(dataset):
    result = lifecycle.build_match_lifecycle()
    assert result["fixtures"] == 3
    assert result["rows"] == 1  # one complete, one incomplete, one missing cache
    assert result["missing_summary"] == 1
    assert result["incomplete"] == 1
    row = lifecycle.lifecycle_for("epl-aaa-bbb-2024-10-19")
    assert row is not None
    assert row.actual_kickoff_utc.isoformat() == "2024-10-19T14:00:00+00:00"
    assert row.actual_end_utc.isoformat() == "2024-10-19T15:50:00+00:00"
    assert row.source == "espn_keyevents"
    assert row.precision == "observed"
    assert result["lifecycle_version"].startswith("sha256:")


def test_rows_without_both_wallclocks_are_omitted(dataset):
    lifecycle.build_match_lifecycle()
    assert lifecycle.lifecycle_for("epl-incomplete-2024-10-21") is None
    assert lifecycle.lifecycle_for("epl-missing-2024-10-22") is None
    assert lifecycle.lifecycle_for("never-seen") is None


def test_phase_boundaries_use_observed_timestamps(dataset):
    lifecycle.build_match_lifecycle()
    row = lifecycle.lifecycle_for("epl-aaa-bbb-2024-10-19")
    kickoff = _utc("2024-10-19T14:00:00Z")
    end = _utc("2024-10-19T15:50:00Z")
    assert lifecycle.phase_at(row, kickoff - timedelta(seconds=1)) == "pre_match"
    assert lifecycle.phase_at(row, kickoff) == "in_play"
    assert lifecycle.phase_at(row, end - timedelta(seconds=1)) == "in_play"
    assert lifecycle.phase_at(row, end) == "post_match"
    assert lifecycle.phase_at(row, end, settlement_ts=end) == "settled"
    assert lifecycle.phase_at(None, kickoff) == "unknown"


def test_market_status_uses_observed_end_and_settlement_boundaries(dataset):
    lifecycle.build_match_lifecycle()
    row = lifecycle.lifecycle_for("epl-aaa-bbb-2024-10-19")
    kickoff = _utc("2024-10-19T14:00:00Z")
    end = _utc("2024-10-19T15:50:00Z")
    settled = _utc("2024-10-19T16:05:00Z")

    assert lifecycle.market_status_at(row, kickoff) == "open"
    assert lifecycle.market_status_at(row, end - timedelta(seconds=1)) == "open"
    assert lifecycle.market_status_at(row, end) == "closed"
    assert lifecycle.market_status_at(row, settled - timedelta(seconds=1), settled) == "closed"
    assert lifecycle.market_status_at(row, settled, settled) == "settled"
    assert lifecycle.market_status_at(None, settled, settled) is None


def test_unknown_phase_disables_duration_helpers(dataset):
    lifecycle.build_match_lifecycle()
    row = lifecycle.lifecycle_for("epl-incomplete-2024-10-21")
    ts = _utc("2024-10-19T13:00:00Z")
    assert lifecycle.phase_at(row, ts) == "unknown"
    assert lifecycle.time_to_kickoff_minutes(row, ts) is None
    assert lifecycle.time_since_kickoff_minutes(row, ts) is None
    assert lifecycle.time_to_settlement_minutes(row, ts, ts) is None


def test_lifecycle_minute_helpers(dataset):
    lifecycle.build_match_lifecycle()
    row = lifecycle.lifecycle_for("epl-aaa-bbb-2024-10-19")
    before = _utc("2024-10-19T13:30:00Z")
    assert lifecycle.time_to_kickoff_minutes(row, before) == 30.0
    assert lifecycle.time_since_kickoff_minutes(row, before) == -30.0
    after = _utc("2024-10-19T14:30:00Z")
    assert lifecycle.time_since_kickoff_minutes(row, after) == 30.0
    assert lifecycle.time_to_settlement_minutes(
        row, after, _utc("2024-10-19T16:00:00Z")
    ) == 90.0
    assert lifecycle.time_to_settlement_minutes(row, after, None) is None


def test_naive_timestamps_are_rejected(dataset):
    lifecycle.build_match_lifecycle()
    row = lifecycle.lifecycle_for("epl-aaa-bbb-2024-10-19")
    with pytest.raises(NaiveTimestampError):
        lifecycle.phase_at(row, datetime(2024, 10, 19, 14, 0))
    with pytest.raises(NaiveTimestampError):
        lifecycle.market_status_at(row, datetime(2024, 10, 19, 14, 0))


def test_lifecycle_can_build_with_limit_and_no_backfill(dataset):
    result = lifecycle.build_match_lifecycle(limit=1, backfill=False)
    assert result["fixtures"] == 1
    assert result["rows"] == 1
    assert result["backfill"] == "offline_cache_only"
