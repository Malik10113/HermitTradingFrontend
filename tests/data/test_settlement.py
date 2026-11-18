from __future__ import annotations

from backend.data import settlement


def test_indexes_published_yes_and_no_values(dataset):
    result = settlement.build_settlement()
    assert result["rows"] >= 2
    row = settlement.get_settlement("cond-home")
    assert row is not None
    assert row.yes_settlement == 1.0
    assert row.no_settlement == 0.0
    assert row.source == "polymarket_markets"
    # markets.parquet says closed with updated_at, so a timestamp is available
    assert row.settled_ts_utc is not None
    assert row.settled_ts_utc.isoformat() == "2024-10-20T09:00:00+00:00"


def test_missing_settlement_returns_none(dataset):
    settlement.build_settlement()
    assert settlement.get_settlement("cond-draw") is None


def test_never_infers_a_complement(dataset):
    settlement.build_settlement()
    row = settlement.get_settlement("cond-only-yes")
    assert row is not None
    assert row.yes_settlement == 1.0
    assert row.no_settlement is None
    assert row.value_for(0) == 1.0
    assert row.value_for(1) is None


def test_open_market_has_no_settled_timestamp(dataset):
    settlement.build_settlement()
    # cond-only-yes has no markets.parquet row at all
    row = settlement.get_settlement("cond-only-yes")
    assert row is not None
    assert row.settled_ts_utc is None


def test_settlement_version_is_recorded(dataset):
    result = settlement.build_settlement()
    assert result["settlement_version"].startswith("sha256:")
    assert settlement.settlement_version() == result["settlement_version"]
    assert settlement.load_settlement()
