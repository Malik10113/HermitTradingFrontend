"""Optional integration test against the real read-only sibling data.

Skipped unless ``HTF_INTEGRATION=1``.  It builds the real instrument index and
one real 1-minute bar file, writing only under ``var/`` (or ``HTF_VAR``).
"""

from __future__ import annotations

import os

import pytest

from backend.data import bars, instruments

pytestmark = pytest.mark.skipif(
    os.environ.get("HTF_INTEGRATION") != "1",
    reason="set HTF_INTEGRATION=1 to run against the sibling data",
)


def test_first_indexed_instrument_builds_one_minute_bars():
    rows = instruments.load_index()
    assert rows, "instrument index is empty"
    candidate = next(
        (row for row in rows if row["token_index"] == 1 and row["trade_count"]),
        None,
    )
    assert candidate is not None, "no NO instrument with tape rows"
    built = bars.build_bars(candidate["instrument_id"], "1m")
    assert built, "expected at least one bar for an instrument present in the tape"
    for bar in built:
        assert 0.0 <= bar["low"] <= bar["high"] <= 1.0
        assert 0.0 <= bar["open"] <= 1.0
        assert 0.0 <= bar["close"] <= 1.0
        assert bar["ts_init"] > bar["ts_event"]
        assert bar["trade_count"] >= 1
        assert bar["data_version"].startswith("sha256:")
