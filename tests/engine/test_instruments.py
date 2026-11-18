from __future__ import annotations

from datetime import datetime, timezone

import pytest
from nautilus_trader.model import AssetClass, BinaryOption, Currency

from backend.engine.instruments import (
    build_binary_option,
    build_pair,
    describe_instrument,
    instrument_id_for,
    pair_ids_for,
    pair_slug,
    validate_price,
    validate_settlement_value,
)

from .conftest import COMPLEMENT_ID, PAIR_ROW, PRIMARY_ID, SLUG, option_row


def test_builds_binary_option_from_index_row():
    instrument = build_binary_option(option_row())

    assert isinstance(instrument, BinaryOption)
    assert str(instrument.id) == PRIMARY_ID
    assert str(instrument.raw_symbol) == "7543210987654321"
    assert str(instrument.currency) == "USD"
    assert instrument.asset_class is AssetClass.ALTERNATIVE
    assert instrument.outcome == "YES"
    assert instrument.description == "Bournemouth vs Arsenal"
    assert instrument.activation_ns == 0
    assert instrument.ts_event == 0
    assert instrument.ts_init == 0
    assert instrument.expiration_ns == int(
        datetime(2024, 10, 19, 17, tzinfo=timezone.utc).timestamp()
    ) * 10**9
    assert instrument.price_precision == 3
    assert str(instrument.price_increment) == "0.001"
    assert instrument.size_precision == 2
    assert str(instrument.size_increment) == "0.01"
    assert str(instrument.min_price) == "0.001"
    assert str(instrument.max_price) == "0.999"


def test_build_binary_option_accepts_epoch_nanoseconds():
    ns = 1_729_274_400_000_000_000
    instrument = build_binary_option(option_row(expiration_ns=ns, end_date_utc=None))
    assert instrument.expiration_ns == ns


def test_build_pair_produces_yes_and_no_tokens():
    primary, complement = build_pair(PAIR_ROW)

    assert str(primary.id) == PRIMARY_ID
    assert str(complement.id) == COMPLEMENT_ID
    assert primary.outcome == "YES"
    assert complement.outcome == "NO"
    assert primary.expiration_ns == complement.expiration_ns
    assert primary.description == complement.description


def test_build_pair_accepts_markets_parquet_token_names():
    row = {
        "condition_id": "0xabc",
        "slug": SLUG,
        "token1": "111",
        "token2": "222",
        "question": "Bournemouth vs Arsenal",
        "end_date": "2024-10-19T17:00:00Z",
    }
    primary, complement = build_pair(row)
    assert str(primary.raw_symbol) == "111"
    assert str(complement.raw_symbol) == "222"


def test_pair_ids_for_either_token():
    assert pair_ids_for(PRIMARY_ID) == (PRIMARY_ID, COMPLEMENT_ID)
    assert pair_ids_for(COMPLEMENT_ID) == (PRIMARY_ID, COMPLEMENT_ID)
    assert pair_slug(PRIMARY_ID) == SLUG


def test_pair_ids_rejects_non_token_id():
    with pytest.raises(ValueError, match="-YES or -NO"):
        pair_ids_for("epl-bou-ars-2024-10-19-bou.POLYMARKET")


def test_instrument_id_for_rejects_unknown_token():
    with pytest.raises(ValueError, match="YES or NO"):
        instrument_id_for(SLUG, "MAYBE")
    with pytest.raises(ValueError, match="slug"):
        instrument_id_for("", "YES")


def test_missing_required_fields_raise():
    row = option_row()
    row.pop("instrument_id")
    with pytest.raises(ValueError, match="instrument_id"):
        build_binary_option(row)

    row = option_row()
    row.pop("symbol")
    with pytest.raises(ValueError, match="raw symbol"):
        build_binary_option(row)

    row = option_row()
    row.pop("end_date_utc")
    with pytest.raises(ValueError, match="expiration"):
        build_binary_option(row)


def test_outcome_must_be_yes_or_no():
    with pytest.raises(ValueError, match="outcome"):
        build_binary_option(option_row(outcome="DRAW"))


def test_naive_expiration_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_binary_option(option_row(end_date_utc="2024-10-19T17:00:00"))


def test_price_precision_and_increment_must_agree():
    with pytest.raises(ValueError, match="price_increment"):
        build_binary_option(option_row(price_precision=2))

    instrument = build_binary_option(
        option_row(price_precision=2, price_increment="0.01", min_price="0.01", max_price="0.99")
    )
    assert str(instrument.price_increment) == "0.01"


def test_size_precision_and_increment_must_agree():
    with pytest.raises(ValueError, match="size_increment"):
        build_binary_option(option_row(size_precision=3))


def test_missing_question_is_allowed_and_description_is_none():
    row = option_row()
    row.pop("question")
    instrument = build_binary_option(row)
    assert instrument.description is None


def test_settlement_values_are_zero_or_one():
    assert validate_settlement_value(0) == 0
    assert validate_settlement_value(0.0) == 0
    assert validate_settlement_value(1) == 1
    assert validate_settlement_value(1.0) == 1
    for bad in (0.5, 2, -1, None, "yes"):
        with pytest.raises(ValueError):
            validate_settlement_value(bad)


def test_price_validation_domain():
    assert validate_price(0.42) == 0.42
    for bad in (-0.01, 1.01, float("nan"), "x"):
        with pytest.raises(ValueError):
            validate_price(bad)


def test_describe_instrument_is_plain_json(primary):
    described = describe_instrument(primary)
    assert described["instrument_id"] == PRIMARY_ID
    assert described["outcome"] == "YES"
    assert described["currency"] == "USD"
    assert described["price_increment"] == "0.001"
    assert isinstance(described["expiration_ns"], int)


def test_currency_is_usd(primary):
    assert primary.currency == Currency.from_str("USD")
