from __future__ import annotations

from backend.data import readmodel


def test_load_bars_returns_iso_page_with_version(dataset):
    page = readmodel.load_bars(dataset.yes_id, "1m")
    assert page["instrument_id"] == dataset.yes_id
    assert page["bar_type"].endswith("-1-MINUTE-LAST-EXTERNAL")
    assert page["data_version"].startswith("sha256:")
    assert len(page["bars"]) == 3
    assert page["has_more"] is False
    first = page["bars"][0]
    assert first["ts_event"].endswith("Z")
    assert first["ts_event_ns"] == 100 * 60 * 1_000_000_000
    assert first["volume_usd"] == 15.0
    assert first["close"] == 0.42


def test_limit_sets_has_more(dataset):
    page = readmodel.load_bars(dataset.yes_id, "1m", limit=2)
    assert len(page["bars"]) == 2
    assert page["has_more"] is True
    page = readmodel.load_bars(dataset.yes_id, "1m", limit=3)
    assert page["has_more"] is False


def test_start_and_end_are_half_open(dataset):
    page = readmodel.load_bars(
        dataset.yes_id,
        "1m",
        start_ns=101 * 60 * 1_000_000_000,
        end_ns=104 * 60 * 1_000_000_000,
    )
    assert [bar["ts_event_ns"] // (60 * 1_000_000_000) for bar in page["bars"]] == [101, 103]


def test_bar_bounds_and_coverage(dataset):
    bounds = readmodel.bar_bounds(dataset.yes_id, "1m")
    assert bounds["bar_count"] == 3
    assert bounds["first_ts_ns"] == 100 * 60 * 1_000_000_000
    assert bounds["last_ts_ns"] == 103 * 60 * 1_000_000_000
    coverage = readmodel.bar_coverage(dataset.yes_id, "1m")
    assert coverage["bar_count"] == 3
    assert coverage["expected_bars"] == 4
    assert coverage["coverage_pct"] == 75.0
    assert readmodel.available_aliases(dataset.yes_id) == ["1m"]


def test_full_bar_type_is_accepted(dataset):
    full = f"{dataset.yes_id}-1-MINUTE-LAST-EXTERNAL"
    page = readmodel.load_bars(dataset.yes_id, full)
    assert len(page["bars"]) == 3
    assert page["bar_type"] == full
    assert readmodel.data_version_for(dataset.yes_id, "1m") == page["data_version"]
