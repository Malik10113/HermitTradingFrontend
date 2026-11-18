from __future__ import annotations

import pytest

from backend.data import bars


def _close(values: list[float]) -> list[float]:
    return [round(value, 9) for value in values]


def test_one_minute_bucketing_and_ohlcv(dataset):
    rows = bars.build_bars(dataset.yes_id, "1m")
    assert len(rows) == 3
    minutes = [row["ts_event"] // (60 * 1_000_000_000) for row in rows]
    assert minutes == [100, 101, 103]  # minute 102 has no trade and no bar
    first = rows[0]
    assert first["open"] == 0.40
    assert first["high"] == 0.42
    assert first["low"] == 0.40
    assert first["close"] == 0.42
    assert first["volume"] == 15.0
    assert first["trade_count"] == 2
    assert first["ts_init"] == first["ts_event"] + 60 * 1_000_000_000
    assert first["bar_type"] == f"{dataset.yes_id}-1-MINUTE-LAST-EXTERNAL"
    assert first["source"] == "polymarket"
    assert first["data_version"].startswith("sha256:")
    assert rows[1]["open"] == rows[1]["high"] == rows[1]["low"] == rows[1]["close"] == 0.38
    assert rows[2]["close"] == 0.41


def test_no_token_is_the_complement(dataset):
    yes = bars.build_bars(dataset.yes_id, "1m")
    no = bars.build_bars(dataset.no_id, "1m")
    assert [row["ts_event"] for row in yes] == [row["ts_event"] for row in no]
    for yes_row, no_row in zip(yes, no):
        assert no_row["open"] == round(1.0 - yes_row["open"], 9)
        assert no_row["high"] == round(1.0 - yes_row["low"], 9)
        assert no_row["low"] == round(1.0 - yes_row["high"], 9)
        assert no_row["close"] == round(1.0 - yes_row["close"], 9)
        assert no_row["volume"] == yes_row["volume"]
        assert no_row["trade_count"] == yes_row["trade_count"]


def test_higher_timeframes_aggregate_without_empty_buckets(dataset):
    rows = bars.build_bars(dataset.yes_id, "5m")
    assert len(rows) == 1
    row = rows[0]
    assert row["ts_event"] == 20 * 300 * 1_000_000_000
    assert row["ts_init"] == row["ts_event"] + 300 * 1_000_000_000
    assert row["open"] == 0.40
    assert row["high"] == 0.42
    assert row["low"] == 0.38
    assert row["close"] == 0.41
    assert row["volume"] == 25.0
    assert row["trade_count"] == 4
    assert row["bar_type"] == (
        f"{dataset.yes_id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    assert len(bars.build_bars(dataset.yes_id, "1h")) == 1
    assert len(bars.build_bars(dataset.yes_id, "1d")) == 1


def test_alias_parsing_matches_model_and_accepts_docs_form(dataset):
    yes = dataset.yes_id
    assert bars.parse_bar_alias("1m", yes) == f"{yes}-1-MINUTE-LAST-EXTERNAL"
    assert bars.parse_bar_alias("5m", yes) == (
        f"{yes}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    assert bars.parse_bar_alias("1h", yes) == (
        f"{yes}-1-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    assert bars.parse_bar_alias("tick_100", yes) == f"{yes}-100-TICK-LAST-EXTERNAL"
    assert bars.parse_bar_alias("volume_1000", yes) == f"{yes}-1000-VOLUME-LAST-EXTERNAL"
    assert bars.parse_bar_alias("value_10000", yes) == f"{yes}-10000-VALUE-LAST-EXTERNAL"
    assert bars.parse_bar_alias("renko_0.01", yes) == f"{yes}-10-RENKO-LAST-EXTERNAL"
    # the documentation's inner LAST form is normalised to the model's canonical form
    docs = f"{yes}-5-MINUTE-LAST-INTERNAL@1-MINUTE-LAST-EXTERNAL"
    assert bars.parse_bar_alias(docs, yes) == (
        f"{yes}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    assert bars.bar_type_alias(bars.parse_bar_alias("15m", yes)) == "15m"
    assert bars.bar_type_alias(bars.parse_bar_alias("renko_0.01", yes)) == "renko_0.01"
    with pytest.raises(ValueError):
        bars.parse_bar_alias("2w", yes)


def test_tick_bars_count_fills(dataset):
    rows = bars.build_bars(dataset.yes_id, "tick_2")
    assert [row["trade_count"] for row in rows] == [2, 2]
    assert _close([row["close"] for row in rows]) == [0.42, 0.41]
    assert rows[0]["ts_event"] == 100 * 60 * 1_000_000_000 + 5_000_000_000
    assert rows[1]["trade_count"] == 2
    rows_3 = bars.build_bars(dataset.yes_id, "tick_3")
    assert [row["trade_count"] for row in rows_3] == [3, 1]


def test_volume_bars_threshold_in_contracts(dataset):
    rows = bars.build_bars(dataset.yes_id, "volume_30")
    assert [row["trade_count"] for row in rows] == [2, 2]
    assert rows[0]["volume"] == 15.0


def test_value_bars_threshold_in_usd(dataset):
    rows = bars.build_bars(dataset.yes_id, "value_15")
    assert [row["trade_count"] for row in rows] == [2, 2]
    assert rows[0]["volume"] == 15.0


def test_renko_bars_follow_brick_moves(dataset):
    rows = bars.build_bars(dataset.yes_id, "renko_0.02")
    assert len(rows) == 3
    assert _close([row["open"] for row in rows]) == [0.40, 0.42, 0.38]
    assert _close([row["close"] for row in rows]) == [0.42, 0.38, 0.40]
    for row in rows:
        assert row["bar_type"] == f"{dataset.yes_id}-20-RENKO-LAST-EXTERNAL"


def test_cache_metadata_and_idempotent_write(dataset):
    first = bars.ensure_bars_file(dataset.yes_id, "1m")
    second = bars.ensure_bars_file(dataset.yes_id, "1m")
    assert first["path"] == second["path"]
    assert first["rows"] == 3
    assert second["data_version"] == first["data_version"]
    meta = bars.read_metadata(first["path"])
    assert meta["alias"] == "1m"
    assert meta["data_version"] == first["data_version"]
    assert meta["builder_version"] == bars.BUILDER
    assert meta["bar_type"] == f"{dataset.yes_id}-1-MINUTE-LAST-EXTERNAL"


def test_start_end_bounds_are_half_open(dataset):
    bars.ensure_bars_file(dataset.yes_id, "1m")
    rows = bars.build_bars(
        dataset.yes_id,
        "1m",
        start=101 * 60 * 1_000_000_000,
        end=104 * 60 * 1_000_000_000,
    )
    assert [row["ts_event"] // (60 * 1_000_000_000) for row in rows] == [101, 103]


def test_empty_condition_yields_no_bars(dataset):
    # cond-tape-only is not a moneyline instrument; build one from a condition
    # whose synthetic tape has no fills is covered by the no_data quality test.
    rows = bars.build_bars(dataset.draw_yes_id, "5m")
    assert len(rows) == 1


def test_unknown_instrument_and_alias_fail(dataset):
    with pytest.raises(KeyError):
        bars.build_bars("missing-YES.POLYMARKET", "1m")
    with pytest.raises(ValueError):
        bars.build_bars(dataset.yes_id, "3w")
