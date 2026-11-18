from __future__ import annotations

from backend.data import instruments


def test_two_tokens_per_moneyline_condition(dataset):
    index = instruments.load_index()
    home_rows = [row for row in index if row["condition_id"] == "cond-home"]
    assert len(home_rows) == 2
    yes = next(row for row in home_rows if row["token_index"] == 0)
    no = next(row for row in home_rows if row["token_index"] == 1)
    assert yes["instrument_id"] == dataset.yes_id
    assert no["instrument_id"] == dataset.no_id
    assert yes["instrument_id"].endswith("-YES.POLYMARKET")
    assert no["instrument_id"].endswith("-NO.POLYMARKET")
    assert yes["token_symbol"] == "token-yes-home"
    assert no["token_symbol"] == "token-no-home"
    assert yes["price_precision"] == 3
    assert yes["selection"] == "home"
    assert yes["settlement_value"] == 1.0
    assert no["settlement_value"] == 0.0
    assert yes["league"] == "epl"
    assert yes["match_key"] == "epl-aaa-bbb-2024-10-19"
    assert yes["home_name"] == "Aaa FC"
    assert yes["away_name"] == "Bbb FC"
    assert yes["first_ts_utc"].isoformat() == "1970-01-01T01:40:05+00:00"
    assert yes["last_ts_utc"].isoformat() == "1970-01-01T01:43:10+00:00"


def test_filters_to_moneyline_markets_present_in_tape(dataset):
    condition_ids = {row["condition_id"] for row in instruments.load_index()}
    assert condition_ids == {"cond-home", "cond-draw"}
    # market with no tape rows and non-moneyline tape condition are excluded
    assert "cond-market-only" not in condition_ids
    assert "cond-tape-only" not in condition_ids
    assert "cond-mismatch" not in condition_ids


def test_duplicate_market_rows_resolve_deterministically(dataset):
    yes = instruments.get_instrument(dataset.yes_id)
    assert yes is not None
    # row id "1" wins over the duplicate id "9"
    assert yes["market_id"] == "1"
    assert yes["question"] == "Will Aaa FC win on 2024-10-19?"

    first = instruments.load_index()
    instruments.refresh_index()
    second = instruments.load_index()
    assert [row["instrument_id"] for row in first] == [row["instrument_id"] for row in second]


def test_pair_lookup(dataset):
    yes = instruments.get_instrument(dataset.yes_id)
    no = instruments.pair_for(dataset.yes_id)
    assert no is not None
    assert no["instrument_id"] == dataset.no_id
    assert no["condition_id"] == yes["condition_id"]
    assert instruments.pair_for("does-not-exist") is None


def test_search_and_league_filter(dataset):
    by_team = instruments.search("Will Aaa FC win")
    assert {row["instrument_id"] for row in by_team} == {dataset.yes_id, dataset.no_id}
    by_question = instruments.search("draw", league="epl")
    assert {row["instrument_id"] for row in by_question} == {
        dataset.draw_yes_id,
        "epl-ccc-ddd-2024-10-20-draw-NO.POLYMARKET",
    }
    assert instruments.search("Aaa FC win", league="lal") == []
    assert instruments.search(limit=1)
    assert len(instruments.search(limit=1)) == 1


def test_missing_settlement_is_null_not_inferred(dataset):
    yes = instruments.get_instrument(dataset.draw_yes_id)
    assert yes is not None
    assert yes["settlement_value"] is None
    assert yes["end_date_utc"] is not None
