"""IndicatorSet behaviour: warm-up, incremental parity, validation, JSON."""

from __future__ import annotations

import pytest

from backend.indicators.compute import (
    IndicatorSet,
    build_indicators,
    canonical_instances,
    compute_series,
    series_to_json,
    spec_hash,
)
from backend.indicators.registry import get_spec, list_specs, validate_params
from tests.indicators.assertions import leading_none_count
from tests.indicators.fixtures.bars import make_bars, sample_instances


def test_compute_series_matches_incremental_indicator_set() -> None:
    bars = make_bars()
    instances = sample_instances()
    batch = compute_series(bars, instances)

    indicator_set = build_indicators(instances)
    incremental = {
        instance_id: {key: [] for key in outputs}
        for instance_id, outputs in indicator_set.outputs.items()
    }
    for bar in bars:
        indicator_set.update(bar)
        for instance_id, values in indicator_set.values().items():
            for key, value in values.items():
                incremental[instance_id][key].append(value)

    assert incremental == batch


def test_values_are_none_until_initialized_then_floats() -> None:
    bars = make_bars()
    indicator_set = build_indicators(
        [{"instance_id": "sma5", "type": "sma", "params": {"period": 5}}]
    )

    for index, bar in enumerate(bars):
        indicator_set.update(bar)
        value = indicator_set.values()["sma5"]["value"]
        initialized = indicator_set.initialized()["sma5"]
        if index < 4:
            assert initialized is False
            assert value is None
        else:
            assert initialized is True
            assert isinstance(value, float)


def test_initialized_map_covers_every_instance() -> None:
    indicator_set = build_indicators(sample_instances())
    flag_map = indicator_set.initialized()
    assert set(flag_map) == {spec.id for spec in list_specs()}
    assert all(value is False for value in flag_map.values())
    assert len(indicator_set) == len(flag_map)


def test_outputs_come_from_the_registry() -> None:
    indicator_set = build_indicators(sample_instances())
    outputs = indicator_set.outputs
    for spec in list_specs():
        assert outputs[spec.id] == [output.key for output in spec.outputs]


def test_macd_returns_value_signal_and_histogram() -> None:
    bars = make_bars()
    series = compute_series(
        bars, [{"instance_id": "macd_fast", "type": "macd", "params": {"fast": 3, "slow": 6, "signal": 3}}]
    )["macd_fast"]
    assert set(series) == {"value", "signal", "histogram"}
    for values in series.values():
        assert len(values) == len(bars)
    for index in range(len(bars)):
        if series["value"][index] is None:
            assert series["signal"][index] is None
            assert series["histogram"][index] is None
        else:
            assert series["histogram"][index] == pytest.approx(
                series["value"][index] - series["signal"][index], rel=0, abs=1e-12
            )


def test_indicator_objects_are_stable_and_per_instance() -> None:
    indicator_set = build_indicators(
        [
            {"instance_id": "fast", "type": "ema", "params": {"period": 3}},
            {"instance_id": "slow", "type": "ema", "params": {"period": 8}},
        ]
    )
    first = indicator_set.indicators
    assert indicator_set.indicators is first
    assert first["fast"] is not first["slow"]
    assert indicator_set.get("fast") is first["fast"]
    assert "fast" in indicator_set
    # Two instances of the same type keep their own periods.
    assert first["fast"].period == 3
    assert first["slow"].period == 8
    with pytest.raises(KeyError):
        indicator_set.get("missing")


def test_warmup_reports_declared_values() -> None:
    indicator_set = build_indicators(
        [
            {"instance_id": "sma9", "type": "sma", "params": {"period": 9}},
            {"instance_id": "vwap", "type": "vwap", "params": {}},
        ]
    )
    assert indicator_set.warmup() == {"sma9": 8, "vwap": 0}


def test_vwap_waits_for_volume() -> None:
    bars = make_bars(6)
    for bar in bars[:3]:
        bar["volume"] = 0.0
    series = compute_series(bars, [{"instance_id": "vwap", "type": "vwap", "params": {}}])[
        "vwap"
    ]["value"]
    assert series[:3] == [None, None, None]
    assert all(isinstance(value, float) for value in series[3:])


def test_unknown_indicator_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown indicator 'nope'"):
        build_indicators([{"instance_id": "x", "type": "nope", "params": {}}])
    with pytest.raises(ValueError, match="unknown indicator 'nope'"):
        compute_series([], [{"instance_id": "x", "type": "nope", "params": {}}])


@pytest.mark.parametrize(
    ("instance", "match"),
    [
        (
            {"instance_id": "s", "type": "sma", "params": {"bogus": 1}},
            "unknown parameter",
        ),
        (
            {"instance_id": "s", "type": "sma", "params": {"period": 1}},
            "sma.period must be >= 2",
        ),
        (
            {"instance_id": "s", "type": "sma", "params": {"period": 501}},
            "sma.period must be <= 500",
        ),
        (
            {"instance_id": "m", "type": "macd", "params": {"fast": 30, "slow": 10}},
            "macd.fast must be smaller than macd.slow",
        ),
        (
            {"instance_id": "b", "type": "bollinger", "params": {"k": "wide"}},
            "bollinger.k must be a number",
        ),
    ],
)
def test_invalid_parameters_are_rejected(instance: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build_indicators([instance])


def test_instance_ids_must_be_unique_and_non_empty() -> None:
    with pytest.raises(ValueError, match="duplicate instance_id 'dup'"):
        build_indicators(
            [
                {"instance_id": "dup", "type": "sma", "params": {}},
                {"instance_id": "dup", "type": "ema", "params": {}},
            ]
        )
    with pytest.raises(ValueError, match="instance_id must be a non-empty string"):
        build_indicators([{"instance_id": "", "type": "sma", "params": {}}])


def test_bar_must_carry_the_fields_the_indicator_needs() -> None:
    indicator_set = build_indicators(
        [{"instance_id": "atr", "type": "atr", "params": {"period": 3}}]
    )
    complete = make_bars(1)[0]
    for field in ("high", "low", "close"):
        bar = dict(complete)
        del bar[field]
        with pytest.raises(ValueError, match=f"missing '{field}'"):
            indicator_set.update(bar)
    with pytest.raises(ValueError, match="bar must be a mapping"):
        indicator_set.update([1, 2, 3])  # type: ignore[arg-type]


def test_non_finite_bar_values_are_rejected() -> None:
    indicator_set = build_indicators(
        [{"instance_id": "sma", "type": "sma", "params": {"period": 3}}]
    )
    bar = make_bars(1)[0]
    bar["close"] = float("nan")
    with pytest.raises(ValueError, match="must be finite"):
        indicator_set.update(bar)
    bar["close"] = None
    with pytest.raises(ValueError, match="is null"):
        indicator_set.update(bar)


def test_empty_inputs_are_well_defined() -> None:
    indicator_set = build_indicators([])
    assert indicator_set.values() == {}
    assert indicator_set.initialized() == {}
    assert indicator_set.warmup() == {}
    assert indicator_set.outputs == {}
    assert len(indicator_set) == 0
    assert compute_series([], sample_instances())["sma"]["value"] == []
    assert compute_series([], []) == {}


def test_series_to_json_preserves_nulls_and_is_a_copy() -> None:
    bars = make_bars(5)
    series = compute_series(
        bars, [{"instance_id": "sma3", "type": "sma", "params": {"period": 3}}]
    )
    payload = series_to_json(series)
    assert payload == series
    assert payload is not series
    assert payload["sma3"]["value"] is not series["sma3"]["value"]
    assert payload["sma3"]["value"][:2] == [None, None]

    payload["sma3"]["value"].append(123.0)
    assert len(series["sma3"]["value"]) == 5


def test_series_to_json_maps_non_finite_values_to_null() -> None:
    payload = series_to_json({"x": {"value": [1.0, float("nan"), float("inf")]}})
    assert payload == {"x": {"value": [1.0, None, None]}}


def test_canonical_instances_fills_defaults_and_sorts() -> None:
    canonical = canonical_instances(
        [
            {"instance_id": "b", "type": "ema", "params": {"period": 5}},
            {"instance_id": "a", "type": "sma", "params": {}},
        ]
    )
    assert [item["instance_id"] for item in canonical] == ["a", "b"]
    assert canonical[0]["params"] == validate_params("sma", {})
    assert canonical[1]["params"] == {"period": 5}


def test_spec_hash_is_stable_and_parameter_sensitive() -> None:
    base = [{"instance_id": "fast", "type": "ema", "params": {"period": 9}}]
    assert spec_hash(base) == spec_hash(
        [{"instance_id": "fast", "type": "ema", "params": {"period": 9}}]
    )
    # Input order and explicit defaults do not change the hash.
    reordered = [
        {"instance_id": "slow", "type": "sma", "params": {"period": 20}},
        {"instance_id": "fast", "type": "ema", "params": {"period": 9}},
    ]
    same = [
        {"instance_id": "fast", "type": "ema", "params": {"period": 9}},
        {"instance_id": "slow", "type": "sma", "params": {}},
    ]
    assert spec_hash(reordered) == spec_hash(same)
    changed = [{"instance_id": "fast", "type": "ema", "params": {"period": 10}}]
    assert spec_hash(base) != spec_hash(changed)
    assert spec_hash(base).startswith("sha256:")
    assert len(spec_hash(base)) == len("sha256:") + 64


def test_registry_warmup_prefix_matches_runtime_for_sma() -> None:
    """Guard the documented convention: warm-up = leading nulls."""
    series = compute_series(
        make_bars(), [{"instance_id": "s", "type": "sma", "params": {"period": 20}}]
    )["s"]["value"]
    spec = get_spec("sma")
    assert leading_none_count(series) == spec.warmup({"period": 20}) == 19


def test_indicator_set_accepts_any_iterable() -> None:
    instance = {"instance_id": "s", "type": "sma", "params": {}}
    assert len(IndicatorSet(iter([instance]))) == 1
    assert len(build_indicators((item for item in [instance]))) == 1
