"""Parity: every registry indicator against a first-principles implementation.

The reference maths lives in ``tests/indicators/reference.py`` and is plain
Python, so this is a comparison of two implementations rather than a snapshot
of the adapter.  No Nautilus object is mocked anywhere.
"""

from __future__ import annotations

import math

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


# ---------------------------------------------------------------------------
# Prediction-market indicators


def _bars_from(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
) -> list[dict[str, float]]:
    """Build probability bars where ``open`` is the previous close."""
    volumes = volumes if volumes is not None else [100.0] * len(closes)
    bars: list[dict[str, float]] = []
    previous: float | None = None
    for close, volume in zip(closes, volumes):
        open_ = close if previous is None else previous
        bars.append(
            {
                "open": open_,
                "high": max(open_, close) + 0.01,
                "low": min(open_, close) - 0.01,
                "close": close,
                "volume": volume,
            }
        )
        previous = close
    return bars


def test_closed_form_clipped_logit_on_every_index() -> None:
    eps = 0.001
    values = _compute("logit", {"eps": eps})["value"]
    assert leading_none_count(values) == 0
    for bar, value in zip(BARS, values):
        probability = min(max(bar["close"], eps), 1.0 - eps)
        assert value == pytest.approx(
            math.log(probability / (1.0 - probability)), rel=0, abs=1e-12
        )


def test_clipped_logit_never_returns_infinities() -> None:
    bars = _bars_from([0.0, 0.5, 1.0, 0.0, 1.0])
    series = compute_series(
        bars, [{"instance_id": "l", "type": "logit", "params": {"eps": 0.001}}]
    )["l"]["value"]
    assert all(value is not None and math.isfinite(value) for value in series)
    assert series[0] == pytest.approx(math.log(0.001 / 0.999), rel=0, abs=1e-12)
    assert series[2] == pytest.approx(-series[0], rel=0, abs=1e-12)
    assert series[1] == pytest.approx(0.0, rel=0, abs=1e-12)


def test_closed_form_probability_change() -> None:
    period = 10
    series = _compute("prob_change", {"period": period})
    for i, bar in enumerate(BARS):
        if i < period:
            assert series["value"][i] is None
            assert series["pct"][i] is None
            continue
        previous = BARS[i - period]["close"]
        delta = bar["close"] - previous
        assert series["value"][i] == pytest.approx(delta, rel=0, abs=1e-12)
        assert series["pct"][i] == pytest.approx(
            delta / previous * 100.0, rel=0, abs=1e-12
        )


def test_probability_change_reports_pct_zero_on_zero_base() -> None:
    bars = _bars_from([0.0, 0.0, 0.5, 0.6])
    series = compute_series(
        bars, [{"instance_id": "p", "type": "prob_change", "params": {"period": 1}}]
    )["p"]
    assert series["value"][1] == pytest.approx(0.0, rel=0, abs=1e-12)
    assert series["pct"][1] == pytest.approx(0.0, rel=0, abs=1e-12)
    assert series["value"][3] == pytest.approx(0.1, rel=0, abs=1e-12)
    assert series["pct"][3] == pytest.approx(20.0, rel=0, abs=1e-12)


def test_closed_form_probability_volatility_uses_sample_std() -> None:
    period = 20
    values = _compute("prob_vol", {"period": period})["value"]
    closes = [bar["close"] for bar in BARS]
    for i in range(len(closes)):
        if i < period:
            assert values[i] is None
            continue
        changes = [closes[j] - closes[j - 1] for j in range(i - period + 1, i + 1)]
        mean = sum(changes) / period
        expected = math.sqrt(
            sum((change - mean) ** 2 for change in changes) / (period - 1)
        )
        assert values[i] == pytest.approx(expected, rel=0, abs=1e-12)


def test_probability_volatility_is_zero_for_a_flat_market() -> None:
    bars = _bars_from([0.5] * 8)
    series = compute_series(
        bars, [{"instance_id": "v", "type": "prob_vol", "params": {"period": 5}}]
    )["v"]["value"]
    assert series[:5] == [None] * 5
    assert all(value == pytest.approx(0.0, rel=0, abs=1e-12) for value in series[5:])


def test_distance_from_extremes_is_consistent_with_the_channel() -> None:
    period = 20
    series = _compute("extreme_dist", {"period": period})
    for i, bar in enumerate(BARS):
        if i < period - 1:
            assert series["from_high"][i] is None
            assert series["from_low"][i] is None
            assert series["position"][i] is None
            continue
        window = BARS[i - period + 1 : i + 1]
        highest = max(item["high"] for item in window)
        lowest = min(item["low"] for item in window)
        span = highest - lowest
        assert series["from_high"][i] == pytest.approx(
            highest - bar["close"], rel=0, abs=1e-12
        )
        assert series["from_low"][i] == pytest.approx(
            bar["close"] - lowest, rel=0, abs=1e-12
        )
        assert series["from_high"][i] + series["from_low"][i] == pytest.approx(
            span, rel=0, abs=1e-12
        )
        assert series["position"][i] == pytest.approx(
            (bar["close"] - lowest) / span, rel=0, abs=1e-12
        )
        assert 0.0 <= series["position"][i] <= 1.0
        assert series["from_high"][i] >= 0.0
        assert series["from_low"][i] >= 0.0


def test_distance_from_extremes_is_neutral_when_the_range_is_empty() -> None:
    bars = _bars_from([0.5] * 6)
    for bar in bars:
        bar["high"] = 0.5
        bar["low"] = 0.5
    series = compute_series(
        bars, [{"instance_id": "e", "type": "extreme_dist", "params": {"period": 3}}]
    )["e"]
    assert series["position"][2:] == [0.5, 0.5, 0.5, 0.5]
    assert series["from_high"][2:] == [0.0, 0.0, 0.0, 0.0]
    assert series["from_low"][2:] == [0.0, 0.0, 0.0, 0.0]


def test_rolling_volume_matches_the_window() -> None:
    period = 20
    series = _compute("rolling_volume", {"period": period})
    for i, bar in enumerate(BARS):
        if i < period - 1:
            assert series["value"][i] is None
            assert series["sum"][i] is None
            assert series["ratio"][i] is None
            continue
        window = [item["volume"] for item in BARS[i - period + 1 : i + 1]]
        total = sum(window)
        mean = total / period
        assert series["sum"][i] == pytest.approx(total, rel=0, abs=1e-12)
        assert series["value"][i] == pytest.approx(mean, rel=0, abs=1e-12)
        assert series["ratio"][i] == pytest.approx(
            window[-1] / mean, rel=0, abs=1e-12
        )
        assert series["ratio"][i] * series["value"][i] == pytest.approx(
            bar["volume"], rel=0, abs=1e-9
        )


def test_rolling_volume_ratio_defaults_to_one_on_zero_mean() -> None:
    bars = _bars_from([0.5] * 5, volumes=[0.0] * 5)
    series = compute_series(
        bars, [{"instance_id": "r", "type": "rolling_volume", "params": {"period": 2}}]
    )["r"]
    assert series["value"][1:] == [0.0, 0.0, 0.0, 0.0]
    assert series["ratio"][1:] == [1.0, 1.0, 1.0, 1.0]


def test_flow_imbalance_is_signed_volume_over_gross_volume() -> None:
    period = 15
    series = _compute("flow_imbalance", {"period": period})
    assert leading_none_count(series["value"]) == period - 1
    for i in range(len(BARS)):
        if i < period - 1:
            assert series["signed_volume"][i] is None
            continue
        net_signed = 0.0
        gross = 0.0
        for j in range(i - period + 1, i + 1):
            item = BARS[j]
            previous_close = BARS[j - 1]["close"] if j > 0 else item["open"]
            if item["close"] > item["open"]:
                sign = 1.0
            elif item["close"] < item["open"]:
                sign = -1.0
            elif item["close"] > previous_close:
                sign = 1.0
            elif item["close"] < previous_close:
                sign = -1.0
            else:
                sign = 0.0
            net_signed += sign * item["volume"]
            gross += item["volume"]
        assert series["signed_volume"][i] == pytest.approx(
            net_signed, rel=0, abs=1e-9
        )
        assert series["value"][i] == pytest.approx(
            net_signed / gross, rel=0, abs=1e-12
        )
        assert -1.0 <= series["value"][i] <= 1.0


def test_flow_imbalance_is_plus_one_when_every_bar_closes_up() -> None:
    bars = [
        {"open": 0.05, "high": 0.11, "low": 0.04, "close": 0.10, "volume": 100.0},
        {"open": 0.10, "high": 0.21, "low": 0.09, "close": 0.20, "volume": 100.0},
        {"open": 0.20, "high": 0.31, "low": 0.19, "close": 0.30, "volume": 100.0},
        {"open": 0.30, "high": 0.41, "low": 0.29, "close": 0.40, "volume": 100.0},
        {"open": 0.40, "high": 0.51, "low": 0.39, "close": 0.50, "volume": 100.0},
    ]
    series = compute_series(
        bars,
        [{"instance_id": "f", "type": "flow_imbalance", "params": {"period": 3}}],
    )["f"]
    assert series["value"][2:] == [1.0, 1.0, 1.0]
    assert series["signed_volume"][2:] == [300.0, 300.0, 300.0]


@pytest.mark.parametrize(
    "indicator_id",
    [
        "logit",
        "prob_change",
        "prob_vol",
        "extreme_dist",
        "rolling_volume",
        "flow_imbalance",
    ],
)
def test_new_registry_warmup_matches_runtime(indicator_id: str) -> None:
    series = _compute(indicator_id)
    first_output = next(iter(series.values()))
    assert _declared_warmup(indicator_id) == leading_none_count(first_output)
    assert set(series) == {
        output.key for output in get_spec(indicator_id).outputs
    }
