from __future__ import annotations

from backend.data import quality


def test_grade_thresholds_are_explicit():
    assert quality.grade_for(2000, 60.0, 120) == "A"
    assert quality.grade_for(1999, 60.0, 120) == "B"
    assert quality.grade_for(500, 30.0, 480) == "B"
    assert quality.grade_for(499, 30.0, 480) == "C"
    assert quality.grade_for(100, 10.0, 1440) == "C"
    assert quality.grade_for(99, 10.0, 1440) == "D"
    assert quality.grade_for(10_000, 5.0, 10) == "D"
    assert quality.grade_for(10_000, 90.0, 5000) == "D"


def test_quality_reports_coverage_gaps_and_trade_stats(dataset):
    report = quality.quality(dataset.yes_id)
    assert report["trade_count"] == 4
    assert report["active_minutes"] == 3
    assert report["span_minutes"] == 4
    assert report["active_minutes_pct"] == 75.0
    assert report["longest_gap_minutes"] == 1
    assert report["median_trade_size_usd"] == 6.0
    assert report["bar_count"] == 3
    assert report["quality_grade"] == "D"  # only four trades
    codes = {warning["code"] for warning in report["warnings"]}
    assert "very_few_trades" in codes
    assert "missing_settlement" not in codes
    assert report["settlement_known"] is True
    assert report["available_from_utc"] == "1970-01-01T01:40:05Z"
    assert report["available_to_utc"] == "1970-01-01T01:43:10Z"


def test_missing_settlement_and_warmup_warnings(dataset):
    report = quality.quality(dataset.draw_yes_id, warmup_bars=10)
    codes = {warning["code"] for warning in report["warnings"]}
    assert "missing_settlement" in codes
    assert "insufficient_warmup" in codes
    assert report["settlement_known"] is False
    assert report["settlement_value"] is None


def test_empty_range_is_explicit(dataset):
    report = quality.quality(
        dataset.yes_id,
        start=1_000 * 60 * 1_000_000_000,
        end=1_001 * 60 * 1_000_000_000,
    )
    assert report["trade_count"] == 0
    assert report["available_from_utc"] is None
    assert report["available_to_utc"] is None
    assert report["active_minutes_pct"] == 0.0
    assert report["bar_count"] == 0
    assert report["quality_grade"] == "D"
    assert {warning["code"] for warning in report["warnings"]} == {"no_data"}


def test_unknown_instrument_fails(dataset):
    try:
        quality.quality("missing-YES.POLYMARKET")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown instrument must raise KeyError")
