from datetime import datetime, timezone
from types import SimpleNamespace

from backend.engine.runner import _effective_expiration_ns, _lifecycle_values


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def test_match_market_uses_the_observed_end_not_the_scheduled_kickoff():
    row = {"end_date_utc": utc(2026, 5, 10, 15, 30)}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "observed_match_end"
    assert ns == int(lifecycle.actual_end_utc.timestamp() * 1_000_000_000)


def test_market_end_wins_when_it_is_already_later():
    row = {"end_date_utc": utc(2026, 12, 31, 23, 0)}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "market_end_date"
    assert ns == int(row["end_date_utc"].timestamp() * 1_000_000_000)


def test_no_lifecycle_keeps_the_market_expiry():
    row = {"end_date_utc": utc(2026, 5, 10, 15, 30)}

    ns, source = _effective_expiration_ns(row, None)

    assert source == "market_end_date"
    assert ns == int(row["end_date_utc"].timestamp() * 1_000_000_000)


def test_existing_expiration_ns_is_honoured():
    row = {"expiration_ns": 1_800_000_000_000_000_000}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "market_end_date"
    assert ns == 1_800_000_000_000_000_000


def test_runner_passes_market_status_to_lifecycle_operands():
    row = SimpleNamespace(
        actual_kickoff_utc=utc(2026, 5, 10, 15, 30),
        actual_end_utc=utc(2026, 5, 10, 17, 0),
    )
    settlement = utc(2026, 5, 10, 17, 5)
    bar_time = utc(2026, 5, 10, 17, 2)

    values = _lifecycle_values(int(bar_time.timestamp() * 1_000_000_000), row, settlement)

    assert values["phase"] == "post_match"
    assert values["market_status"] == "closed"
    assert values["time_to_settlement_minutes"] == 3.0
