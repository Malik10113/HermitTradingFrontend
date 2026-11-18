"""Parity: every registry indicator against a first-principles implementation.

The reference maths lives in ``tests/indicators/reference.py`` and is plain
Python, so this is a comparison of two implementations rather than a snapshot
of the adapter.  No Nautilus object is mocked anywhere.
"""

from __future__ import annotations

import pytest

from backend.indicators import compute_series, get_spec, list_specs, validate_params
from backend.indicators.compute import build_indicators
from tests.indicators.assertions import assert_values_close, leading_none_count
from tests.indicators.fixtures.bars import make_bars, sample_instances
from tests.indicators.reference import reference_series

BARS = make_bars()
SPECS = list_specs()


def _compute(indicator_id: str, params: dict[str, float] | None = None) -> dict:
    series = compute_series(
        BARS, [{"instance_id": indicator_id, "type": indicator_id, "params": params or {}}]
    )
    return series[indicator_id]


def _declared_warmup(indicator_id: str, params: dict[str, float] | None = None) -> int:
    return max(int(get_spec(indicator_id).warmup(validate_params(indicator_id, params))), 0)


def _runtime_warmup(indicator_id: str, params: dict[str, float] | None = None) -> int:
    values = _compute(indicator_id, params)["value"] if indicator_id != "stochastic" else _compute(indicator_id, params)["k"]
    return leading_none_count(values)


@pytest.mark.parametrize("spec", SPECS, ids=lambda spec: spec.id)
def test_every_registry_indicator_matches_reference(spec) -> None:
    params = validate_params(spec.id, {})
    actual = _compute(spec.id)
    expected = reference_series(spec.id, params, BARS)

    assert set(actual) == {output.key for output in spec.outputs}
    for key, expected_values in expected.items():
        assert_values_close(
            actual[key], expected_values, context=f"{spec.id}.{key}"
        )


@pytest.mark.parametrize("spec", SPECS, ids=lambda spec: spec.id)
def test_warmup_is_exactly_none_then_float(spec) -> None:
    params = validate_params(spec.id, {})
    actual = _compute(spec.id)
    expected = reference_series(spec.id, params, BARS)

    for key, values in actual.items():
        warmup = leading_none_count(values)
        assert warmup == leading_none_count(expected[key]), (
            f"{spec.id}.{key}: runtime warm-up {warmup} differs from reference "
            f"{leading_none_count(expected[key])}"
        )
        assert warmup < len(values), f"{spec.id}.{key} never initialised"
        assert all(value is None for value in values[:warmup])
        assert isinstance(values[warmup], float), (
            f"{spec.id}.{key}: first initialised value must be a float, "
            f"got {type(values[warmup]).__name__}"
        )
        # A warm-up null is never a fabricated zero or a sentinel.
        assert all(
            value is None or isinstance(value, float) for value in values
        )


def test_closed_form_sma_on_every_index() -> None:
    period = 5
    values = _compute("sma", {"period": period})["value"]
    for i in range(len(BARS)):
        if i < period - 1:
            assert values[i] is None
            continue
        window = [bar["close"] for bar in BARS[i - period + 1 : i + 1]]
        assert values[i] == pytest.approx(sum(window) / period, rel=0, abs=1e-12)


def test_closed_form_wma_on_every_index() -> None:
    period = 5
    denominator = period * (period + 1) / 2
    values = _compute("wma", {"period": period})["value"]
    for i in range(len(BARS)):
        if i < period - 1:
            assert values[i] is None
            continue
        window = [bar["close"] for bar in BARS[i - period + 1 : i + 1]]
        expected = sum((j + 1) * value for j, value in enumerate(window)) / denominator
        assert values[i] == pytest.approx(expected, rel=0, abs=1e-12)


def test_closed_form_bollinger_bounds_and_middle() -> None:
    period, k = 20, 2.0
    series = _compute("bollinger", {"period": period, "k": k})
    for i in range(len(BARS)):
        if i < period - 1:
            assert series["middle"][i] is None
            assert series["upper"][i] is None
            assert series["lower"][i] is None
            continue
        window = [bar["close"] for bar in BARS[i - period + 1 : i + 1]]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / period
        deviation = variance**0.5
        assert series["middle"][i] == pytest.approx(mean, rel=0, abs=1e-12)
        assert series["upper"][i] == pytest.approx(mean + k * deviation, rel=0, abs=1e-12)
        assert series["lower"][i] == pytest.approx(mean - k * deviation, rel=0, abs=1e-12)
        assert series["upper"][i] - series["middle"][i] == pytest.approx(
            series["middle"][i] - series["lower"][i], rel=0, abs=1e-12
        )


def test_closed_form_donchian_upper_lower() -> None:
    period = 20
    series = _compute("donchian", {"period": period})
    for i in range(len(BARS)):
        if i < period - 1:
            assert series["upper"][i] is None
            assert series["lower"][i] is None
            assert series["middle"][i] is None
            continue
        window = BARS[i - period + 1 : i + 1]
        highest = max(bar["high"] for bar in window)
        lowest = min(bar["low"] for bar in window)
        assert series["upper"][i] == pytest.approx(highest, rel=0, abs=1e-12)
        assert series["lower"][i] == pytest.approx(lowest, rel=0, abs=1e-12)
        assert series["middle"][i] == pytest.approx(
            (highest + lowest) / 2, rel=0, abs=1e-12
        )


def test_vwap_is_cumulative_close_volume_ratio() -> None:
    values = _compute("vwap")["value"]
    cumulative_value = 0.0
    cumulative_volume = 0.0
    for bar, value in zip(BARS, values):
        cumulative_value += bar["close"] * bar["volume"]
        cumulative_volume += bar["volume"]
        assert value == pytest.approx(
            cumulative_value / cumulative_volume, rel=0, abs=1e-12
        )


def test_indicator_set_warmup_reports_registry_metadata() -> None:
    indicator_set = build_indicators(sample_instances())
    expected = {
        spec.id: max(int(spec.warmup(validate_params(spec.id, {}))), 0)
        for spec in SPECS
    }
    assert indicator_set.warmup() == expected


def test_registry_macd_warmup_matches_runtime() -> None:
    assert _declared_warmup("macd") == _runtime_warmup("macd")


def test_registry_stochastic_warmup_matches_runtime() -> None:
    assert _declared_warmup("stochastic") == _runtime_warmup("stochastic")
