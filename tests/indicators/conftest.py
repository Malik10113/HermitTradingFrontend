"""Fixtures for the indicator domain: deterministic bars and instances."""

from __future__ import annotations

import pytest

from tests.indicators.fixtures.bars import (
    BAR_COUNT,
    make_bars,
    make_bar,
    sample_instances,
)


@pytest.fixture(scope="session")
def bars() -> list[dict[str, float]]:
    return make_bars()


@pytest.fixture(scope="session")
def instances() -> list[dict[str, object]]:
    return sample_instances()


__all__ = ["BAR_COUNT", "bars", "instances", "make_bar", "make_bars"]
