"""Deterministic fixtures for the indicator domain.

The bar series is generated, not loaded: indicator tests must run offline and
must not depend on the sibling data repository.  Prices are probabilities in
``[0, 1]`` like the real Polymarket bars, and every field is an exact float so
the fixtures are reproducible across machines.
"""

from __future__ import annotations

from typing import Any

BAR_COUNT = 60


def make_bar(index: int, previous_close: float | None = None) -> dict[str, float]:
    close = round(0.40 + 0.30 * (((index * 37) % 61) / 60.0), 6)
    open_ = close if previous_close is None else previous_close
    high = round(max(open_, close) + 0.005 + 0.001 * (index % 5), 6)
    low = round(min(open_, close) - 0.005 - 0.001 * ((index * 3) % 5), 6)
    volume = round(100.0 + ((index * 13) % 47), 3)
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def make_bars(count: int = BAR_COUNT) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    previous: float | None = None
    for index in range(count):
        bar = make_bar(index, previous)
        bars.append(bar)
        previous = bar["close"]
    return bars


def sample_instances() -> list[dict[str, Any]]:
    """One instance per registry indicator, with default parameters."""
    from backend.indicators.registry import list_specs

    return [
        {"instance_id": spec.id, "type": spec.id, "params": {}}
        for spec in list_specs()
    ]
