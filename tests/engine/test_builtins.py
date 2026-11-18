from __future__ import annotations

import copy

import pytest

from backend.indicators.registry import get_spec, validate_params
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    ExecutionConfig,
    RiskConfig,
    RunConfig,
    StrategySection,
)
from backend.strategies import BUILTINS, build, catalogue, get_builtin
from backend.strategies import builtins as builtins_module

EXPECTED_IDS = [
    "nautilus_ema_cross",
    "sma_trend",
    "rsi_reversion",
    "bollinger_reversion",
    "macd_trend",
    "donchian_breakout",
    "atr_vol_breakout",
    "vwap_reversion",
    "buy_and_hold",
    "random_entry",
]


def reference_run(strategy: dict) -> RunConfig:
    return RunConfig(
        strategy=StrategySection(**strategy),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        evaluation=EvaluationConfig(date_range=DateRange(start="2024-10-01", end="2026-10-01")),
    )


def test_catalogue_has_exactly_the_ten_builtins():
    entries = catalogue()
    assert [entry["id"] for entry in entries] == EXPECTED_IDS
    assert len(BUILTINS) == 10


def test_every_builtin_validates_through_run_config():
    for entry in catalogue():
        config = reference_run(entry["rules"])
        assert config.strategy.instrument_id == entry["rules"]["instrument_id"]
        assert config.strategy.bar_type == entry["rules"]["bar_type"]


def test_every_builtin_has_provenance_and_description():
    for entry in catalogue():
        assert entry["provenance"].strip(), entry["id"]
        assert entry["description"].strip(), entry["id"]
        assert entry["label"].strip(), entry["id"]
        assert entry["params"] == entry["params"]  # JSON-serialisable list
        assert isinstance(entry["null_baseline"], bool)


def test_every_builtin_uses_registry_indicators_and_valid_params():
    for entry in catalogue():
        for instance in entry["rules"]["indicators"]:
            spec = get_spec(instance["type"])
            assert spec.id == instance["type"], entry["id"]
            validated = validate_params(instance["type"], instance["params"])
            assert validated == instance["params"], entry["id"]
            outputs = {output.key for output in spec.outputs}
            for field in ("long_entry", "long_exit", "short_entry", "short_exit"):
                tree = entry["rules"][field]
                if tree is not None:
                    _assert_refs_known(tree, entry["rules"]["indicators"], outputs, entry["id"])


def _assert_refs_known(tree, indicators, outputs, builtin_id):
    if not isinstance(tree, dict) or len(tree) != 1:
        raise AssertionError(f"{builtin_id}: malformed node {tree!r}")
    key, value = next(iter(tree.items()))
    if key in ("all", "any"):
        for child in value:
            _assert_refs_known(child, indicators, outputs, builtin_id)
        return
    if key == "not":
        _assert_refs_known(value, indicators, outputs, builtin_id)
        return
    for operand in value:
        if isinstance(operand, str) and operand.startswith("indicator:"):
            _, instance_id, output = operand.split(":")
            assert instance_id in {i["instance_id"] for i in indicators}, builtin_id
            assert output in outputs, (builtin_id, operand)


def test_builtins_are_deterministic():
    for entry in catalogue():
        first = build(entry["id"], "some-slug-YES.POLYMARKET", "1m")
        second = build(entry["id"], "some-slug-YES.POLYMARKET", "1m")
        assert first == second
        assert copy.deepcopy(first) == second


def test_defaults_are_inside_the_declared_ranges():
    for entry in catalogue():
        for param in entry["params"]:
            default = entry["defaults"][param["key"]]
            if param["min"] is not None:
                assert default >= param["min"], (entry["id"], param["key"])
            if param["max"] is not None:
                assert default <= param["max"], (entry["id"], param["key"])
            assert isinstance(default, (int, float))


def test_unknown_builtin_is_rejected():
    with pytest.raises(ValueError, match="unknown built-in"):
        get_builtin("no_such_strategy")


def test_unknown_parameter_is_rejected():
    with pytest.raises(ValueError, match="unknown parameter"):
        build("nautilus_ema_cross", "x-YES.POLYMARKET", params={"magic": 1})


def test_out_of_range_parameter_is_rejected():
    with pytest.raises(ValueError, match="fast_period"):
        build("nautilus_ema_cross", "x-YES.POLYMARKET", params={"fast_period": 1})
    with pytest.raises(ValueError, match="fast_period"):
        build(
            "nautilus_ema_cross",
            "x-YES.POLYMARKET",
            params={"fast_period": 50, "slow_period": 20},
        )


def test_parameters_are_applied():
    rules = build(
        "nautilus_ema_cross",
        "x-YES.POLYMARKET",
        params={"fast_period": 5, "slow_period": 15},
    )
    periods = {i["instance_id"]: i["params"]["period"] for i in rules["indicators"]}
    assert periods == {"ema_fast": 5, "ema_slow": 15}


def test_buy_and_hold_holds_to_expiry():
    rules = build("buy_and_hold", "x-YES.POLYMARKET")
    assert rules["settlement"] == "hold_to_expiry"
    assert rules["direction"] == "long"
    assert rules["indicators"] == []
    assert rules["long_entry"] is not None
    assert rules["long_exit"] is None


def test_random_entry_is_a_labelled_null_baseline():
    entry = next(e for e in catalogue() if e["id"] == "random_entry")
    assert entry["null_baseline"] is True
    assert "null" in entry["description"].lower()
    # No runtime randomness or code evaluation anywhere in the catalogue.
    source = open(builtins_module.__file__).read()
    assert "eval(" not in source
    assert "random." not in source
    assert "import random" not in source


def test_random_entry_alternates_direction_from_a_mean_cross():
    rules = build("random_entry", "x-YES.POLYMARKET", params={"period": 10})
    assert rules["long_entry"] == {"gt": ["close", "indicator:block_mean:value"]}
    assert rules["short_entry"] == {"lt": ["close", "indicator:block_mean:value"]}
    assert rules["indicators"][0]["params"] == {"period": 10}


def test_hold_to_expiry_builtins_do_not_declare_exits():
    rules = build("buy_and_hold", "x-YES.POLYMARKET")
    assert rules["long_exit"] is None and rules["short_entry"] is None


def test_catalogue_accepts_a_real_instrument_id():
    entries = catalogue(instrument_id="real-slug-YES.POLYMARKET", bar_type="5m")
    for entry in entries:
        assert entry["rules"]["instrument_id"] == "real-slug-YES.POLYMARKET"
        assert entry["rules"]["bar_type"] == "5m"


def test_catalogue_entries_are_json_serialisable():
    import json

    payload = json.dumps(catalogue())
    assert "nautilus_ema_cross" in payload
    assert "provenance" in payload


def test_single_day_evaluation_range_is_valid_but_a_reversed_one_is_not():
    import pytest as _pytest

    from backend.rules.schema import DateRange

    same_day = DateRange(start="2026-09-19", end="2026-09-19")
    assert same_day.start == same_day.end
    with _pytest.raises(ValueError):
        DateRange(start="2026-09-20", end="2026-09-19")
