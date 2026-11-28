"""Portfolio optimisation tests: Kelly sizing, weight caps and composition."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from backend.engine.portfolio import (
    PORTFOLIO_METHODS,
    KellyConfig,
    cap_weights,
    compute_weights,
    kelly_stake_fraction,
    optimize_runs,
    three_stage_allocate,
)

DAY_NS = 86_400 * 1_000_000_000
MINUTE_NS = 60 * 1_000_000_000
START_NS = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

LOW_VOL = [0.002, -0.001, 0.0015, -0.0005] * 10
HIGH_VOL = [-0.02, 0.01, -0.015, 0.005] * 10
INDEPENDENT = [0.005, 0.004, -0.003, -0.002] * 10


def _curve(
    returns: list[float],
    *,
    start_ns: int = START_NS,
    step_ns: int = DAY_NS,
    base: float = 1000.0,
) -> list[dict[str, float | int]]:
    equity = base
    points: list[dict[str, float | int]] = [{"ts_ns": start_ns, "equity_usd": equity}]
    for position, value in enumerate(returns):
        equity *= 1.0 + value
        points.append(
            {"ts_ns": start_ns + (position + 1) * step_ns, "equity_usd": equity}
        )
    return points


def _run(run_id: str, returns: list[float], **overrides: object) -> dict[str, object]:
    run: dict[str, object] = {
        "run_id": run_id,
        "instrument_id": f"instrument-{run_id}",
        "strategy_hash": f"hash-{run_id}",
        "requested_profile": "Conservative",
        "effective_profile": "Conservative",
        "equity": _curve(returns),
        "metrics": None,
    }
    run.update(overrides)
    return run


def _assert_weight_vector(
    weights: dict[str, float], *, max_weight: float, total: float | None = None
) -> None:
    for value in weights.values():
        assert math.isfinite(value)
        assert 0.0 <= value <= max_weight + 1e-12
    deployed = sum(weights.values())
    assert deployed <= 1.0 + 1e-12
    if total is not None:
        assert deployed == pytest.approx(total, abs=1e-9)


# ---------------------------------------------------------------------------
# Kelly stake sizing
# ---------------------------------------------------------------------------


def test_kelly_returns_zero_below_min_periods() -> None:
    config = KellyConfig(min_periods=5)
    assert kelly_stake_fraction([0.02, 0.01, 0.03, 0.01], config=config) == 0.0


def test_kelly_returns_zero_without_positive_lcb() -> None:
    # A positive raw mean whose standard error swamps it is not evidence.
    returns = [0.01, -0.01] * 10
    assert sum(returns) == pytest.approx(0.0)
    assert kelly_stake_fraction(returns) == 0.0
    assert kelly_stake_fraction([-0.01] * 20) == 0.0


def test_kelly_caps_at_max_stake() -> None:
    assert kelly_stake_fraction([0.05] * 20) == pytest.approx(0.05)


def test_kelly_applies_min_stake_floor_once_edge_exists() -> None:
    # Positive LCB but a raw Kelly fraction below the floor: the floor applies.
    returns = [1.2] * 30 + [-0.8] * 30
    config = KellyConfig(min_stake=0.03, max_stake=0.05, min_periods=5)
    assert kelly_stake_fraction(returns, config=config) == pytest.approx(0.03)


def test_kelly_reads_only_the_trailing_window() -> None:
    returns = [-0.1] * 100 + [0.02] * 5
    short = KellyConfig(window=5, min_periods=5)
    long = KellyConfig(window=200, min_periods=5)
    assert kelly_stake_fraction(returns, config=short) > 0.0
    assert kelly_stake_fraction(returns, config=long) == 0.0


def test_kelly_config_validates_its_bounds() -> None:
    with pytest.raises(ValueError):
        KellyConfig(max_stake=0.0)
    with pytest.raises(ValueError):
        KellyConfig(min_stake=0.1, max_stake=0.05)
    with pytest.raises(ValueError):
        KellyConfig(min_periods=3, window=2)


# ---------------------------------------------------------------------------
# Weight caps
# ---------------------------------------------------------------------------


def test_cap_weights_waterfills_excess() -> None:
    weights = cap_weights({"a": 0.6, "b": 0.3, "c": 0.1}, 0.4)
    assert weights == pytest.approx({"a": 0.4, "b": 0.4, "c": 0.2}, abs=1e-12)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_cap_weights_keeps_cash_residual_when_cap_binds() -> None:
    weights = cap_weights({key: 1.0 for key in ("a", "b", "c", "d")}, 0.15)
    assert weights == pytest.approx(
        {"a": 0.15, "b": 0.15, "c": 0.15, "d": 0.15}, abs=1e-12
    )
    assert sum(weights.values()) == pytest.approx(0.6)


def test_cap_weights_zero_mass_stays_cash() -> None:
    assert cap_weights({"a": 0.0, "b": -1.0}, 0.5) == {"a": 0.0, "b": 0.0}


def test_cap_weights_rejects_invalid_cap() -> None:
    with pytest.raises(ValueError):
        cap_weights({"a": 1.0}, 0.0)


# ---------------------------------------------------------------------------
# Weighting rules
# ---------------------------------------------------------------------------


def test_all_methods_are_exported() -> None:
    assert PORTFOLIO_METHODS == (
        "equal",
        "inverse_volatility",
        "risk_parity",
        "exponential",
        "shrunk_mean_variance",
        "three_stage",
    )
    with pytest.raises(ValueError):
        compute_weights({"a": LOW_VOL}, method="not_a_rule")


def test_equal_weight_is_uniform_then_capped() -> None:
    series = {"low": LOW_VOL, "high": HIGH_VOL, "indep": INDEPENDENT}
    uncapped = compute_weights(series, method="equal", max_weight=1.0)
    assert uncapped == pytest.approx(
        {"low": 1 / 3, "high": 1 / 3, "indep": 1 / 3}
    )
    capped = compute_weights(series, method="equal", max_weight=0.3)
    _assert_weight_vector(capped, max_weight=0.3, total=0.9)


def test_inverse_volatility_favours_the_calmer_sleeve() -> None:
    weights = compute_weights(
        {"low": LOW_VOL, "high": HIGH_VOL}, method="inverse_volatility", max_weight=1.0
    )
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert weights["low"] > weights["high"] > 0.0


def test_risk_parity_sums_to_one_and_favours_the_calmer_sleeve() -> None:
    weights = compute_weights(
        {"low": LOW_VOL, "indep": INDEPENDENT}, method="risk_parity", max_weight=1.0
    )
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert weights["low"] > weights["indep"] > 0.0


def test_exponential_weights_decayed_positive_means_only() -> None:
    series = {
        "up": [0.01, 0.005, -0.001] * 10,
        "down": [-0.01, -0.005, -0.001] * 10,
    }
    weights = compute_weights(series, method="exponential", max_weight=1.0)
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert weights["up"] == pytest.approx(1.0)
    assert weights["down"] == 0.0

    all_down = compute_weights(
        {"down": series["down"]}, method="exponential", max_weight=1.0
    )
    assert all_down == {"down": 0.0}


def test_shrunk_mean_variance_prefers_the_positive_mean_sleeve() -> None:
    series = {
        "up": [0.01, 0.005, -0.001] * 10,
        "down": [-0.01, -0.005, -0.001] * 10,
    }
    weights = compute_weights(series, method="shrunk_mean_variance", max_weight=1.0)
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert weights["up"] == pytest.approx(1.0)
    assert weights["down"] == 0.0


def test_shrunk_mean_variance_falls_back_to_inverse_volatility() -> None:
    series = {
        "down_low": [-0.001, -0.002, -0.001, -0.002] * 10,
        "down_high": [-0.02, -0.01, -0.015, -0.005] * 10,
    }
    weights = compute_weights(series, method="shrunk_mean_variance", max_weight=1.0)
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert weights["down_low"] > weights["down_high"] > 0.0


# ---------------------------------------------------------------------------
# Three-stage allocator
# ---------------------------------------------------------------------------


def test_three_stage_collapses_near_duplicates_keeping_the_calmer_sleeve() -> None:
    signal = [0.01, -0.004, 0.006, -0.002, 0.003, -0.001] * 6
    series = {
        "calm": [value * 0.5 for value in signal],
        "noisy": [value * 2.0 for value in signal],
        "indep": [0.002, -0.003, 0.001, 0.004, -0.002, 0.0] * 6,
    }
    allocation = three_stage_allocate(series, max_weight=1.0)
    assert allocation["weights"]["noisy"] == 0.0
    assert "noisy" in allocation["dropped"]
    assert allocation["reasons"]["noisy"] == "correlation >= 0.95 with calm"
    assert allocation["weights"]["calm"] > 0.0
    assert allocation["reasons"].get("calm") is None
    assert allocation["reallocated"] > 0.0
    assert set(allocation["kept"]) == {"calm", "indep"}
    _assert_weight_vector(allocation["weights"], max_weight=1.0, total=1.0)


def test_three_stage_penalises_correlated_survivors() -> None:
    series = {"low": LOW_VOL, "indep": INDEPENDENT, "high": HIGH_VOL}
    allocation = three_stage_allocate(series, max_weight=1.0)
    weights = allocation["weights"]
    _assert_weight_vector(weights, max_weight=1.0, total=1.0)
    assert allocation["dropped"] == []
    assert all(weights[name] > 0.0 for name in series)
    # The low-volatility sleeve keeps the largest share of the book.
    assert max(weights, key=weights.get) == "low"
    assert 0.0 <= allocation["max_pair_correlation"] <= 1.0
    squared = sum(value * value for value in weights.values())
    assert allocation["effective_n"] == pytest.approx(1.0 / squared)


def test_three_stage_marks_sleeves_without_history() -> None:
    allocation = three_stage_allocate(
        {"thin": [0.01], "low": LOW_VOL}, max_weight=1.0
    )
    assert allocation["weights"]["thin"] == 0.0
    assert allocation["reasons"]["thin"] == "fewer than 2 return observations"


# ---------------------------------------------------------------------------
# End-to-end composition
# ---------------------------------------------------------------------------


def test_optimize_runs_composes_multi_day_equity_curves() -> None:
    runs = [
        _run(
            "r1",
            [0.010, -0.006, 0.004, 0.002, -0.003, 0.001] * 7,
            metrics={"net_pnl_usd": 41.25},
        ),
        _run("r2", [-0.004, 0.007, -0.002, 0.005, 0.001, -0.005] * 7),
        _run("r3", [0.002, 0.003, -0.006, -0.001, 0.004, 0.002] * 7),
    ]
    result = optimize_runs(runs, max_weight=0.35, bankroll_usd=1000.0)

    assert result["method"] == "three_stage"
    assert result["methodology"] == "daily_returns"
    assert "kelly" in result["provenance"]
    assert "weighting" in result["provenance"]
    assert "allocation" in result["provenance"]

    weights = result["weights"]
    assert list(weights) == ["r1", "r2", "r3"]
    assert all(0.0 <= value <= 0.35 + 1e-12 for value in weights.values())
    assert 1.0 <= result["effective_n"] <= 3.0

    matrix = result["correlation_matrix"]
    for left in weights:
        assert matrix[left][left] == 1.0
        for right in weights:
            assert -1.0 <= matrix[left][right] <= 1.0
            assert matrix[left][right] == pytest.approx(matrix[right][left])

    assert len(result["equity_index"]) == 43
    assert result["equity_index"][0] == {
        "ts": "2024-10-01T00:00:00Z",
        "equity_index": 100.0,
    }
    timestamps = [point["ts"] for point in result["equity_index"]]
    assert timestamps == sorted(timestamps)

    metrics = result["metrics"]
    assert metrics["starting_cash_usd"] == 1000.0
    assert metrics["alignment"] == "daily_returns"
    assert metrics["observations"] == 42
    assert metrics["days"] == 43
    assert metrics["net_pnl_usd"] == pytest.approx(
        metrics["final_equity_usd"] - 1000.0
    )
    assert metrics["return_pct"] == pytest.approx(
        metrics["net_pnl_usd"] / 1000.0 * 100.0
    )
    assert metrics["max_drawdown_pct"] <= 0.0
    assert result["equity_index"][-1]["equity_index"] == pytest.approx(
        metrics["final_equity_usd"] / 10.0
    )

    assert 0.0 <= result["portfolio_kelly_fraction"] <= 0.05
    assert result["recommended_daily_stake_usd"] == pytest.approx(
        round(1000.0 * result["portfolio_kelly_fraction"], 2)
    )
    assert result["reallocated_weight"] >= 0.0

    assert len(result["members"]) == 3
    members = {member["run_id"]: member for member in result["members"]}
    assert set(result["kelly_fractions"]) == {"r1", "r2", "r3"}
    assert members["r1"]["net_pnl_usd"] == pytest.approx(41.25)
    assert members["r2"]["weight"] == pytest.approx(weights["r2"])
    for member in result["members"]:
        assert 0.0 <= member["kelly_fraction"] <= 0.05
        assert member["annualised_vol"] is None or member["annualised_vol"] >= 0.0
        assert member["dropped"] == (member["weight"] <= 0.0)

    assert isinstance(result["warnings"], list)


def test_optimize_runs_aligns_intraday_bar_returns() -> None:
    runs = [
        {
            "run_id": "m1",
            "instrument_id": "match-yes",
            "equity": _curve(
                [0.001, -0.0005, 0.0008, -0.0003] * 30, step_ns=MINUTE_NS
            ),
        },
        {
            "run_id": "m2",
            "instrument_id": "match-no",
            "equity": _curve(
                [0.0004, 0.0006, -0.0005, -0.0002] * 30, step_ns=MINUTE_NS
            ),
        },
    ]
    result = optimize_runs(runs)

    assert result["methodology"] == "intraday_bar_returns"
    assert result["metrics"]["alignment"] == "intraday_bar_returns"
    assert result["metrics"]["days"] == 1
    assert result["metrics"]["observations"] == 120
    assert result["equity_index"][0]["equity_index"] == 100.0
    assert any(
        warning.startswith("intraday_bar_returns:") for warning in result["warnings"]
    )
    assert len(result["equity_index"]) == 121
    assert sum(result["weights"].values()) <= 1.0 + 1e-12
    assert result["correlation_matrix"]["m1"]["m1"] == 1.0


def test_optimize_runs_excludes_a_flat_equity_curve() -> None:
    runs = [
        _run("moves", [0.01, -0.004, 0.006, -0.002] * 10),
        _run("flat", [0.0] * 40),
    ]
    result = optimize_runs(runs)

    assert result["weights"]["flat"] == 0.0
    member = {item["run_id"]: item for item in result["members"]}["flat"]
    assert member["dropped"] is True
    assert "flat" in (member["drop_reason"] or "")
    assert any("flat equity curve" in warning for warning in result["warnings"])


def test_optimize_runs_rejects_empty_duplicate_and_historyless_inputs() -> None:
    with pytest.raises(ValueError):
        optimize_runs([])
    duplicate = [_run("same", [0.01, -0.004] * 5), _run("same", [0.01, -0.004] * 5)]
    with pytest.raises(ValueError):
        optimize_runs(duplicate)
    with pytest.raises(ValueError):
        optimize_runs([{"run_id": "empty", "equity": []}])


def test_optimize_runs_reports_undeployed_cash_residual() -> None:
    runs = [_run("only", [0.01, -0.004, 0.006, -0.002] * 10)]
    result = optimize_runs(runs, max_weight=0.35)
    assert result["weights"]["only"] == pytest.approx(0.35)
    assert any(
        "undeployed cash residual" in warning for warning in result["warnings"]
    )
