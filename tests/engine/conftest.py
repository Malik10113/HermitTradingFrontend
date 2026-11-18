"""Shared fixtures for the engine domain tests.

Everything is synthetic: no network, no sibling-repo data, no ``var/``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine.instruments import build_binary_option, build_pair, instrument_id_for

SLUG = "epl-bou-ars-2024-10-19-bou"
PRIMARY_ID = instrument_id_for(SLUG, "YES")
COMPLEMENT_ID = instrument_id_for(SLUG, "NO")
END_DATE_UTC = "2024-10-19T17:00:00Z"

PAIR_ROW = {
    "slug": SLUG,
    "token_yes": "7543210987654321",
    "token_no": "7543210987654322",
    "question": "Bournemouth vs Arsenal",
    "end_date_utc": END_DATE_UTC,
    "price_precision": 3,
}


@pytest.fixture
def pair():
    return build_pair(PAIR_ROW)


@pytest.fixture
def primary(pair):
    return pair[0]


@pytest.fixture
def complement(pair):
    return pair[1]


class _BarType:
    """Minimal stand-in for a Nautilus ``BarType`` in unit tests."""

    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id

    def __str__(self) -> str:
        return f"{self.instrument_id}-1-MINUTE-LAST-EXTERNAL"


def make_bar(ts_event: int, close: float, *, instrument_id: str = PRIMARY_ID, **overrides):
    data = {
        "bar_type": _BarType(instrument_id),
        "ts_event": int(ts_event),
        "ts_init": int(ts_event) + 60 * 10**9,
        "open": float(close),
        "high": float(close) + 0.01,
        "low": float(close) - 0.01,
        "close": float(close),
        "volume": 100.0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_quote(ts: int, *, bid: float, ask: float, instrument_id: str = PRIMARY_ID):
    return SimpleNamespace(
        instrument_id=instrument_id,
        bid_price=bid,
        ask_price=ask,
        bid_size=50.0,
        ask_size=50.0,
        ts_event=int(ts),
        ts_init=int(ts),
    )


def option_row(**overrides):
    row = {
        "instrument_id": PRIMARY_ID,
        "symbol": "7543210987654321",
        "outcome": "YES",
        "question": "Bournemouth vs Arsenal",
        "end_date_utc": END_DATE_UTC,
        "price_precision": 3,
    }
    row.update(overrides)
    return row


def sample_instrument(**overrides):
    return build_binary_option(option_row(**overrides))
