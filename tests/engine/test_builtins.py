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
    "flow_momentum",
    "large_trade_follow",
    "short_horizon_reversion",
    "logit_reversion",
    "prob_vol_breakout",
]

PREDICTION_MARKET_IDS = [
    "flow_momentum",
    "large_trade_follow",
    "short_horizon_reversion",
    "logit_reversion",
    "prob_vol_breakout",
]


def wiring(rules: dict) -> dict[str, tuple[str, dict]]:
    return {
        indicator["instance_id"]: (indicator["type"], indicator["params"])
        for indicator in rules["indicators"]
    }


def reference_run(strategy: dict) -> RunConfig:
    return RunConfig(
        strategy=StrategySection(**strategy),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        evaluation=EvaluationConfig(date_range=DateRange(start="2024-10-01", end="2026-10-01")),
    )


def test_catalogue_has_exactly_the_fifteen_builtins():
    entries = catalogue()
    assert [entry["id"] for entry in entries] == EXPECTED_IDS
    assert len(BUILTINS) == 15


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
        outputs_by_instance: dict[str, set[str]] = {}
        for instance in entry["rules"]["indicators"]:
            spec = get_spec(instance["type"])
            assert spec.id == instance["type"], entry["id"]
            validated = validate_params(instance["type"], instance["params"])
            assert validated == instance["params"], entry["id"]
            outputs_by_instance[instance["instance_id"]] = {
                output.key for output in spec.outputs
            }
        for field in ("long_entry", "long_exit", "short_entry", "short_exit"):
            tree = entry["rules"][field]
            if tree is not None:
                _assert_refs_known(tree, outputs_by_instance, entry["id"])


def _assert_refs_known(tree, outputs_by_instance, builtin_id):
    if not isinstance(tree, dict) or len(tree) != 1:
        raise AssertionError(f"{builtin_id}: malformed node {tree!r}")
    key, value = next(iter(tree.items()))
    if key in ("all", "any"):
        for child in value:
            _assert_refs_known(child, outputs_by_instance, builtin_id)
        return
    if key == "not":
        _assert_refs_known(value, outputs_by_instance, builtin_id)
        return
    for operand in value:
        if isinstance(operand, str) and operand.startswith("indicator:"):
            _, instance_id, output = operand.split(":")
            assert instance_id in outputs_by_instance, builtin_id
            assert output in outputs_by_instance[instance_id], (builtin_id, operand)


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


# ---------------------------------------------------------------------------
# Prediction-market templates ported from FundedBetBacktesting


def test_prediction_market_builtins_use_rule_exit_and_carry_provenance():
    for builtin_id in PREDICTION_MARKET_IDS:
        builtin = get_builtin(builtin_id)
        rules = build(builtin_id, "x-YES.POLYMARKET")
        assert rules["direction"] == "both", builtin_id
        assert rules["settlement"] == "rule_exit", builtin_id
        assert "prediction_market" in builtin.tags, builtin_id
        assert "FundedBetBacktesting" in builtin.provenance, builtin_id
        for field_name in ("long_entry", "long_exit", "short_entry", "short_exit"):
            assert rules[field_name] is not None, (builtin_id, field_name)


def test_flow_momentum_wiring_and_rules():
    rules = build("flow_momentum", "x-YES.POLYMARKET")
    assert wiring(rules) == {
        "dp": ("prob_change", {"period": 15}),
        "flow": ("flow_imbalance", {"period": 15}),
    }
    assert rules["long_entry"] == {
        "all": [
            {"gte": ["indicator:dp:value", 0.02]},
            {"gte": ["indicator:flow:value", 0.15]},
        ]
    }
    assert rules["long_exit"] == {
        "any": [
            {"lte": ["indicator:dp:value", 0.0]},
            {"lte": ["indicator:flow:value", 0.0]},
        ]
    }
    assert rules["short_entry"] == {
        "all": [
            {"lte": ["indicator:dp:value", -0.02]},
            {"lte": ["indicator:flow:value", -0.15]},
        ]
    }
    assert rules["short_exit"] == {
        "any": [
            {"gte": ["indicator:dp:value", 0.0]},
            {"gte": ["indicator:flow:value", 0.0]},
        ]
    }


def test_flow_momentum_applies_parameters():
    rules = build(
        "flow_momentum",
        "x-YES.POLYMARKET",
        params={"mom_period": 5, "flow_period": 9, "min_move": 0.05, "min_imbalance": 0.5},
    )
    assert wiring(rules) == {
        "dp": ("prob_change", {"period": 5}),
        "flow": ("flow_imbalance", {"period": 9}),
    }
    assert rules["long_entry"]["all"][0] == {"gte": ["indicator:dp:value", 0.05]}
    assert rules["long_entry"]["all"][1] == {"gte": ["indicator:flow:value", 0.5]}
    assert rules["short_entry"]["all"][0] == {"lte": ["indicator:dp:value", -0.05]}


def test_large_trade_follow_wiring_and_rules():
    rules = build("large_trade_follow", "x-YES.POLYMARKET")
    assert wiring(rules) == {
        "rvol": ("rolling_volume", {"period": 30}),
        "flow": ("flow_imbalance", {"period": 5}),
        "sma_exit": ("sma", {"period": 30}),
    }
    assert rules["long_entry"] == {
        "all": [
            {"gte": ["indicator:rvol:ratio", 2.5]},
            {"gte": ["indicator:flow:value", 0.25]},
        ]
    }
    assert rules["long_exit"] == {
        "any": [
            {"lt": ["close", "indicator:sma_exit:value"]},
            {"lte": ["indicator:flow:value", -0.25]},
        ]
    }
    assert rules["short_entry"] == {
        "all": [
            {"gte": ["indicator:rvol:ratio", 2.5]},
            {"lte": ["indicator:flow:value", -0.25]},
        ]
    }
    assert rules["short_exit"] == {
        "any": [
            {"gt": ["close", "indicator:sma_exit:value"]},
            {"gte": ["indicator:flow:value", 0.25]},
        ]
    }


def test_short_horizon_reversion_wiring_and_rules():
    rules = build("short_horizon_reversion", "x-YES.POLYMARKET")
    assert wiring(rules) == {
        "dp": ("prob_change", {"period": 15}),
        "ext": ("extreme_dist", {"period": 15}),
        "mid_sma": ("sma", {"period": 15}),
    }
    assert rules["long_entry"] == {
        "all": [
            {"lte": ["indicator:dp:value", -0.03]},
            {"lte": ["indicator:ext:position", 0.2]},
        ]
    }
    assert rules["long_exit"] == {"gte": ["close", "indicator:mid_sma:value"]}
    assert rules["short_entry"] == {
        "all": [
            {"gte": ["indicator:dp:value", 0.03]},
            {"gte": ["indicator:ext:position", 0.8]},
        ]
    }
    assert rules["short_exit"] == {"lte": ["close", "indicator:mid_sma:value"]}


def test_short_horizon_reversion_rejects_overlapping_zones():
    with pytest.raises(ValueError, match="low_zone"):
        build(
            "short_horizon_reversion",
            "x-YES.POLYMARKET",
            params={"low_zone": 0.5, "high_zone": 0.5},
        )
    with pytest.raises(ValueError, match="low_zone"):
        build(
            "short_horizon_reversion",
            "x-YES.POLYMARKET",
            params={"low_zone": 0.7, "high_zone": 0.6},
        )


def test_logit_reversion_wiring_and_rules():
    rules = build("logit_reversion", "x-YES.POLYMARKET")
    assert wiring(rules) == {
        "log_p": ("logit", {"eps": 0.001}),
        "rsi": ("rsi", {"period": 14}),
        "mean_p": ("sma", {"period": 20}),
    }
    assert rules["long_entry"] == {
        "all": [
            {"lte": ["indicator:log_p:value", -1.5]},
            {"lt": ["indicator:rsi:value", 35.0]},
        ]
    }
    assert rules["long_exit"] == {"gte": ["close", "indicator:mean_p:value"]}
    assert rules["short_entry"] == {
        "all": [
            {"gte": ["indicator:log_p:value", 1.5]},
            {"gt": ["indicator:rsi:value", 65.0]},
        ]
    }
    assert rules["short_exit"] == {"lte": ["close", "indicator:mean_p:value"]}


def test_logit_reversion_clips_logit_with_epsilon():
    rules = build("logit_reversion", "x-YES.POLYMARKET", params={"eps": 0.01})
    assert wiring(rules)["log_p"] == ("logit", {"eps": 0.01})


def test_prob_vol_breakout_wiring_and_rules():
    rules = build("prob_vol_breakout", "x-YES.POLYMARKET")
    assert wiring(rules) == {
        "pvol_fast": ("prob_vol", {"period": 10}),
        "pvol_slow": ("prob_vol", {"period": 40}),
        "ext": ("extreme_dist", {"period": 20}),
        "trend_sma": ("sma", {"period": 20}),
    }
    assert rules["long_entry"] == {
        "all": [
            {"gt": ["indicator:pvol_fast:value", "indicator:pvol_slow:value"]},
            {"gte": ["indicator:ext:position", 0.90]},
        ]
    }
    assert rules["long_exit"] == {"lt": ["close", "indicator:trend_sma:value"]}
    assert rules["short_entry"] == {
        "all": [
            {"gt": ["indicator:pvol_fast:value", "indicator:pvol_slow:value"]},
            {"lte": ["indicator:ext:position", 0.10]},
        ]
    }
    assert rules["short_exit"] == {"gt": ["close", "indicator:trend_sma:value"]}


def test_prob_vol_breakout_requires_fast_vol_below_baseline():
    with pytest.raises(ValueError, match="fast_vol_period"):
        build(
            "prob_vol_breakout",
            "x-YES.POLYMARKET",
            params={"fast_vol_period": 40, "slow_vol_period": 40},
        )
    with pytest.raises(ValueError, match="fast_vol_period"):
        build(
            "prob_vol_breakout",
            "x-YES.POLYMARKET",
            params={"fast_vol_period": 60, "slow_vol_period": 40},
        )

