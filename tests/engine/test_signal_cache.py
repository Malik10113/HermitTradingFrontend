"""Signal-plan cache tests (acceptance criterion 19).

Everything is synthetic: no network, no sibling data.  The parity tests use the
real ``IndicatorSet`` (Nautilus SMA) and the real evaluator; the runner test
uses a stubbed data layer so ``execute_run`` still builds a real Nautilus
engine, real fills and real artifacts-in-memory.
"""

from __future__ import annotations

import json
import threading

import pytest

from backend.engine.rule_strategy import RuleStrategy
from backend.engine.signals import (
    SignalCache,
    SignalPlan,
    compute_signal_plan,
    declared_warmup,
    make_signal_key,
    signal_spec_hash,
)
from backend.indicators.compute import INDICATOR_IMPL_VERSION
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    ExecutionConfig,
    IndicatorInstance,
    RiskConfig,
    RunConfig,
    SizingConfig,
    StrategySection,
    run_hash,
    strategy_hash,
)

from .conftest import COMPLEMENT_ID, PRIMARY_ID, SLUG, make_bar, make_quote

TS0 = 1_700_006_220_000_000_000  # 2023-11-14T23:57:00Z, spans midnight
BAR = 60 * 10**9
BAR_TYPE = f"{PRIMARY_ID}-1-MINUTE-LAST-EXTERNAL"


# ---------------------------------------------------------------------------
# Helpers


def make_config(
    *,
    long_entry=None,
    long_exit=None,
    short_entry=None,
    short_exit=None,
    indicators=None,
    direction="both",
    settlement="rule_exit",
    execution=None,
    sizing=None,
    bar_type=BAR_TYPE,
    start="2024-10-01",
    end="2026-10-01",
) -> RunConfig:
    return RunConfig(
        strategy=StrategySection(
            instrument_id=PRIMARY_ID,
            bar_type=bar_type,
            indicators=indicators or [],
            long_entry=long_entry,
            long_exit=long_exit,
            short_entry=short_entry,
            short_exit=short_exit,
            direction=direction,
            settlement=settlement,
        ),
        execution=execution or ExecutionConfig(profile="conservative"),
        risk=RiskConfig(sizing=sizing or SizingConfig()),
        evaluation=EvaluationConfig(date_range=DateRange(start=start, end=end)),
    )


def ma(period: int = 3, instance_id: str = "ma") -> IndicatorInstance:
    return IndicatorInstance(instance_id=instance_id, type="sma", params={"period": period})


def bar_row(ts: int, close: float) -> dict:
    return {
        "ts_event_ns": int(ts),
        "ts_init_ns": int(ts) + BAR,
        "open": float(close),
        "high": float(close) + 0.01,
        "low": float(close) - 0.01,
        "close": float(close),
        "volume": 100.0,
        "trade_count": 10,
    }


class RecordingStrategy(RuleStrategy):
    """The real strategy; only venue submission is recorded."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted: list[dict] = []

    def _submit_market(self, instrument, side, quantity, ts_ns):
        self.submitted.append(
            {
                "instrument_id": str(instrument.id),
                "side": side,
                "quantity": float(quantity),
                "ts_ns": ts_ns,
            }
        )
        self._diagnostics["orders_submitted"] += 1


def cross_config(*, period: int = 3, **kwargs) -> RunConfig:
    return make_config(
        indicators=[ma(period)],
        long_entry={"crosses_above": ["close", "indicator:ma:value"]},
        long_exit={"crosses_below": ["close", "indicator:ma:value"]},
        short_entry={"crosses_below": ["close", "indicator:ma:value"]},
        short_exit={"crosses_above": ["close", "indicator:ma:value"]},
        **kwargs,
    )


def plan_for(config, bars, *, data_version="sha256:dv1", lifecycle=None, lifecycle_version=None):
    return compute_signal_plan(
        config,
        bars,
        lifecycle or (lambda _ts: {"phase": "in_play", "time_to_kickoff_minutes": -5.0}),
        data_version=data_version,
        lifecycle_version=lifecycle_version,
    )


# ---------------------------------------------------------------------------
# Plan / runtime parity


def test_plan_and_runtime_make_the_same_decisions(primary, complement):
    config = cross_config()
    closes = [0.40, 0.45, 0.55, 0.52, 0.44, 0.47, 0.60, 0.50, 0.41]
    stamps = [TS0 + index * BAR for index in range(len(closes))]
    bars = [bar_row(ts, close) for ts, close in zip(stamps, closes)]
    plan = plan_for(config, bars)

    # Warm-up is explicit and nothing is true while the SMA operand is None.
    assert plan.warmup_bars == 2
    assert plan.rows[0].indicator_values["ma"]["value"] is None
    assert plan.rows[0].long_entry_ok is False
    assert plan.rows[0].long_exit_ok is False
    assert plan.rows[0].short_entry_ok is False
    assert plan.rows[0].short_exit_ok is False

    runtime = RecordingStrategy(config, primary, complement)
    planned = RecordingStrategy(config, primary, complement, signal_plan=plan)
    for ts, close in zip(stamps, closes):
        bar = make_bar(ts, close)
        runtime.on_bar(bar)
        planned.on_bar(bar)
        assert runtime.signals == planned.signals
        assert runtime.pending_intent == planned.pending_intent
        # Every truth matches the runtime evaluator, not just the final action.
        row = plan.row_at(ts)
        if runtime.pending_intent is not None:
            assert row.truth(runtime.pending_intent.action) is True

    assert planned.signals, "expected at least one signal"
    assert planned.diagnostics()["signals_generated"] == runtime.diagnostics()["signals_generated"]
    assert planned.diagnostics()["bars"] == runtime.diagnostics()["bars"]
    # Indicator values recorded on a signal are the plan snapshot.
    assert planned.signals == [
        {
            "ts_ns": signal["ts_ns"],
            "action": signal["action"],
            "ref_price": signal["ref_price"],
            "indicator_values": {
                "ma": {"value": plan.row_at(signal["ts_ns"]).indicator_values["ma"]["value"]}
            },
        }
        for signal in planned.signals
    ]


def test_plan_requires_a_matching_strategy_hash(primary, complement):
    config = cross_config()
    bars = [bar_row(TS0, 0.4), bar_row(TS0 + BAR, 0.5)]
    plan = plan_for(config, bars)
    other = make_config(long_entry={"gt": ["close", 0.1]}, direction="long")

    with pytest.raises(ValueError, match="different strategy"):
        RuleStrategy(other, primary, complement, signal_plan=plan)


def test_plan_path_rejects_a_bar_outside_the_plan(primary, complement):
    config = cross_config()
    plan = plan_for(config, [bar_row(TS0, 0.4)])
    strategy = RecordingStrategy(config, primary, complement, signal_plan=plan)
    strategy.on_bar(make_bar(TS0, 0.4))
    with pytest.raises(ValueError, match="not in the signal plan"):
        strategy.on_bar(make_bar(TS0 + BAR, 0.5))


# ---------------------------------------------------------------------------
# Cache key and hits/misses


def test_cache_hit_when_only_execution_and_sizing_change(primary, complement):
    base = cross_config()
    variant = base.model_copy(
        update={
            "execution": ExecutionConfig(
                profile="stress", slippage_bps=25, min_abs_slippage=0.002
            ),
            "risk": RiskConfig(sizing=SizingConfig(mode="fixed_stake", value=777.0, bankroll_usd=12345.0)),
            "evaluation": EvaluationConfig(date_range=DateRange(start="2025-01-01", end="2025-06-01")),
        }
    )

    # Criterion 19: same strategy hash, different run hash.
    assert strategy_hash(base) == strategy_hash(variant)
    assert run_hash(base, "sha256:dv1", "sha256:code") != run_hash(
        variant, "sha256:dv1", "sha256:code"
    )

    bars = [bar_row(TS0, 0.4), bar_row(TS0 + BAR, 0.5), bar_row(TS0 + 2 * BAR, 0.3)]
    cache = SignalCache(capacity=4)
    key_a = make_signal_key(
        data_version="sha256:dv1",
        bar_type=base.strategy.bar_type,
        strategy_hash=strategy_hash(base),
        warmup_bars=declared_warmup(base.strategy.indicators),
    )
    lookup_a = cache.get_or_compute(key_a, lambda: plan_for(base, bars))
    assert lookup_a.hit is False
    assert lookup_a.source == "none"
    plan_a = lookup_a.plan

    key_b = make_signal_key(
        data_version="sha256:dv1",
        bar_type=variant.strategy.bar_type,
        strategy_hash=strategy_hash(variant),
        warmup_bars=declared_warmup(variant.strategy.indicators),
    )
    assert key_a == key_b
    lookup_b = cache.get_or_compute(
        key_b,
        lambda: pytest.fail("a profile change must not recompute the plan"),
    )
    assert lookup_b.hit is True
    assert lookup_b.source == "memory"
    assert lookup_b.plan is plan_a
    stats = cache.stats()
    assert (stats["hits"], stats["misses"], stats["size"], stats["capacity"]) == (1, 1, 1, 4)
    assert stats["disk"]["enabled"] is False

    # No execution, sizing, fee or profile value is part of the plan.
    metadata = json.dumps(plan_a.metadata(), sort_keys=True)
    for forbidden in ("stress", "slippage", "fee", "bankroll", "sizing", "777", "12345"):
        assert forbidden not in metadata


def test_cache_key_components_are_exactly_the_brief_key():
    key = make_signal_key(
        data_version="sha256:dv",
        bar_type="1m",
        strategy_hash="sha256:strategy",
        warmup_bars=19,
        indicator_impl_version="1",
    )
    assert key._fields == (
        "data_version",
        "bar_type",
        "strategy_hash",
        "warmup_bars",
        "indicator_impl_version",
    )
    assert key == (
        "sha256:dv",
        "1m",
        "sha256:strategy",
        19,
        "1",
    )
    assert signal_spec_hash(key).startswith("sha256:")
    assert signal_spec_hash(key) == signal_spec_hash(
        make_signal_key(
            data_version="sha256:dv",
            bar_type="1m",
            strategy_hash="sha256:strategy",
            warmup_bars=19,
            indicator_impl_version="1",
        )
    )


def test_cache_misses_when_any_key_component_changes():
    config = cross_config()
    bars = [bar_row(TS0, 0.4), bar_row(TS0 + BAR, 0.5)]
    plan = plan_for(config, bars)
    cache = SignalCache(capacity=8)
    cache.put(plan.key, plan)

    assert cache.get(plan.key) is plan
    variants = [
        plan.key._replace(data_version="sha256:dv2"),
        plan.key._replace(bar_type="5m"),
        plan.key._replace(strategy_hash="sha256:other"),
        plan.key._replace(warmup_bars=plan.key.warmup_bars + 1),
        plan.key._replace(indicator_impl_version="2"),
    ]
    for variant in variants:
        assert variant != plan.key
        assert cache.get(variant) is None

    # An indicator parameter change is a new strategy hash and a new warm-up.
    changed = cross_config(period=5)
    changed_bars = [bar_row(TS0 + index * BAR, 0.4) for index in range(6)]
    changed_plan = plan_for(changed, changed_bars)
    assert changed_plan.strategy_hash != plan.strategy_hash
    assert changed_plan.warmup_bars == 4
    cache.put(changed_plan.key, changed_plan)
    assert cache.get(changed_plan.key) is changed_plan


def test_cache_rejects_a_plan_from_another_lifecycle_version():
    config = cross_config()
    bars = [bar_row(TS0, 0.4)]
    cache = SignalCache(capacity=2)
    plan = plan_for(config, bars, lifecycle_version="sha256:life-a")
    cache.put(plan.key, plan)

    assert cache.get(plan.key, lifecycle_version="sha256:life-a") is plan
    assert cache.get(plan.key, lifecycle_version="sha256:life-b") is None
    # Unknown lifecycle versions are accepted, never invented.
    assert cache.get(plan.key, lifecycle_version=None) is plan


# ---------------------------------------------------------------------------
# Determinism


def test_plan_hash_is_stable_and_ignores_execution_and_sizing():
    config = cross_config()
    bars = [bar_row(TS0 + index * BAR, close) for index, close in enumerate([0.4, 0.4, 0.4, 0.5])]
    first = plan_for(config, bars)
    second = plan_for(config, bars)
    assert first.plan_hash == second.plan_hash
    assert first.key == second.key

    variant = config.model_copy(
        update={
            "execution": ExecutionConfig(profile="book", book_staleness_s=5),
            "risk": RiskConfig(sizing=SizingConfig(mode="risk_pct", value=2.0, bankroll_usd=500.0)),
        }
    )
    third = plan_for(variant, bars)
    assert third.key == first.key
    assert third.plan_hash == first.plan_hash

    changed_data = plan_for(config, bars, data_version="sha256:dv-other")
    assert changed_data.plan_hash != first.plan_hash

    # Truth counts are diagnostics only; they belong to the plan.
    assert first.true_counts["long_entry"] >= 1
    assert set(first.true_counts) == {"long_entry", "long_exit", "short_entry", "short_exit"}


# ---------------------------------------------------------------------------
# LRU and concurrency


def _plan_with_threshold(threshold: float) -> SignalPlan:
    config = make_config(long_entry={"gt": ["close", threshold]}, direction="long")
    bars = [bar_row(TS0, 0.6)]
    return plan_for(config, bars)


def test_lru_evicts_the_oldest_entry():
    cache = SignalCache(capacity=2)
    plans = [_plan_with_threshold(0.1), _plan_with_threshold(0.2), _plan_with_threshold(0.3)]
    for plan in plans:
        cache.put(plan.key, plan)
    assert len(cache) == 2
    assert cache.get(plans[0].key) is None
    assert cache.stats()["misses"] == 1
    assert plans[2].key in cache
    assert plans[1].key in cache
    assert plans[0].key not in cache
    assert cache.stats()["size"] == 2


def test_cache_is_thread_safe():
    cache = SignalCache(capacity=2)
    plan = _plan_with_threshold(0.1)
    results: list[SignalPlan] = []
    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(25):
                got = cache.get_or_compute(plan.key, lambda: plan).plan
                results.append(got)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(results) == 200
    assert all(got.key == plan.key for got in results)
    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["hits"] + stats["misses"] == 200
    assert stats["misses"] >= 1


# ---------------------------------------------------------------------------
# Runner manifest (criterion 19, through execute_run)


def _instrument_row(instrument_id: str, outcome: str) -> dict:
    return {
        "instrument_id": instrument_id,
        "symbol": "7543210987654321",
        "token_symbol": "7543210987654321",
        "outcome": outcome,
        "condition_id": SLUG,
        "match_key": SLUG,
        "slug": SLUG,
        "question": "Bournemouth vs Arsenal",
        "end_date_utc": "2024-10-19T17:00:00Z",
        "price_precision": 3,
    }


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """Stub the data layer; the engine, fills and analytics stay real."""
    from backend.data import bars as bars_module
    from backend.data import books as books_module
    from backend.data import instruments as instruments_module
    from backend.data import lifecycle as lifecycle_module
    from backend.data import readmodel, settlement
    from backend.engine import runner as runner_module
    from backend.engine import signals as signals_module

    primary_closes = [0.40, 0.50, 0.50, 0.40, 0.40]
    complement_closes = [0.60] * len(primary_closes)

    def fake_load(instrument_id, alias="1m", start_ns=None, end_ns=None, limit=None):
        closes = primary_closes if instrument_id == PRIMARY_ID else complement_closes
        rows = [
            bar_row(TS0 + index * BAR, close) for index, close in enumerate(closes)
        ]
        if start_ns is not None:
            rows = [row for row in rows if row["ts_event_ns"] >= start_ns]
        if end_ns is not None:
            rows = [row for row in rows if row["ts_event_ns"] < end_ns]
        if limit is not None:
            rows = rows[:limit]
        return {"bars": rows, "bar_type": alias, "data_version": "sha256:test"}

    monkeypatch.setattr(runner_module, "_ensure_derived", lambda: None)
    monkeypatch.setattr(runner_module, "run_data_version", lambda: "sha256:test")
    monkeypatch.setattr(runner_module, "code_version", lambda: "sha256:code")
    monkeypatch.setattr(runner_module, "_lifecycle_version", lambda: None)
    monkeypatch.setattr(bars_module, "build_bars", lambda *args, **kwargs: None)
    monkeypatch.setattr(readmodel, "load_bars", fake_load)
    monkeypatch.setattr(
        books_module,
        "book_coverage",
        lambda *args, **kwargs: {"has_coverage": False, "window": {}},
    )
    monkeypatch.setattr(
        instruments_module,
        "get_instrument",
        lambda instrument_id: _instrument_row(
            instrument_id, "YES" if instrument_id == PRIMARY_ID else "NO"
        ),
    )
    monkeypatch.setattr(lifecycle_module, "lifecycle_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(settlement, "get_settlement", lambda *args, **kwargs: None)
    # A fresh worker process starts with an empty memory LRU but the same disk
    # store; isolate it in tmp_path so the test never touches var/.
    cache = signals_module.SignalCache(
        store=signals_module.SignalPlanStore(root=tmp_path / "signals")
    )
    monkeypatch.setattr(signals_module, "DEFAULT_SIGNAL_CACHE", cache)
    try:
        yield cache
    finally:
        cache.clear(disk=True)


def test_runner_reuses_a_disk_plan_across_worker_like_runs(runner_env):
    from backend.engine.runner import execute_run

    conservative = make_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
        execution=ExecutionConfig(profile="conservative"),
        bar_type="1m",
        start="2023-11-14",
        end="2023-11-15",
    )
    frictionless = conservative.model_copy(
        update={"execution": ExecutionConfig(profile="frictionless")}
    )

    first = execute_run(conservative.model_dump(mode="json"))
    assert first.status == "complete"
    # Simulate the next worker: it has no memory of the first run.
    runner_env.clear()
    second = execute_run(frictionless.model_dump(mode="json"))
    assert second.status == "complete"

    manifest_a, manifest_b = first.manifest, second.manifest
    cache_a, cache_b = manifest_a["signal_cache"], manifest_b["signal_cache"]
    assert cache_a["hit"] is False
    assert cache_a["source"] == "none"
    assert cache_b["hit"] is True
    assert cache_b["source"] == "disk"

    # Criterion 19: same strategy hash and plan, different run hash and costs.
    assert manifest_a["strategy_hash"] == manifest_b["strategy_hash"]
    assert manifest_a["run_hash"] != manifest_b["run_hash"]
    assert cache_a["plan_hash"] == cache_b["plan_hash"]
    assert cache_a["key"] == cache_b["key"]
    assert cache_a["spec_hash"] == cache_b["spec_hash"]
    assert cache_a["key"]["strategy_hash"] == manifest_a["strategy_hash"]
    assert cache_a["rows"] == cache_b["rows"] == 5
    assert cache_a["warmup_bars"] == cache_b["warmup_bars"] == 0
    assert cache_a["key"]["indicator_impl_version"] == INDICATOR_IMPL_VERSION

    result_a, result_b = first.result, second.result
    # The cached plan path produces the same signal rows as the computed path.
    assert result_a["signals"] == result_b["signals"]
    assert result_a["signals"], "expected signals from the synthetic bars"
    assert result_a["metrics"]["net_pnl_usd"] != result_b["metrics"]["net_pnl_usd"]

    stats = runner_env.stats()
    assert stats["disk"]["hits"] == 1
    assert stats["disk"]["entries"] == 1
    assert stats["disk"]["bytes"] > 0


def test_runner_reuses_a_plan_across_different_date_windows(runner_env):
    """The plan key omits the evaluation window, so it must cover every bar.

    ``fake_load`` honours ``start_ns``/``end_ns``; the first run sees the three
    bars before midnight and the second sees the two after it. A plan computed
    over only the first window would not contain the second window's bars and
    ``RuleStrategy.on_bar`` would raise ``is not in the signal plan``.
    """
    from backend.engine.runner import execute_run

    first_window = make_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
        execution=ExecutionConfig(profile="conservative"),
        bar_type="1m",
        start="2023-11-14",
        end="2023-11-14",
    )
    second_window = first_window.model_copy(
        update={
            "evaluation": EvaluationConfig(
                date_range=DateRange(start="2023-11-15", end="2023-11-15")
            )
        }
    )
    assert strategy_hash(first_window) == strategy_hash(second_window)

    first = execute_run(first_window.model_dump(mode="json"))
    assert first.status == "complete"
    assert first.manifest["signal_cache"]["hit"] is False

    second = execute_run(second_window.model_dump(mode="json"))
    assert second.status == "complete"
    assert second.manifest["signal_cache"]["hit"] is True
    # Same strategy, same plan, different window and run hash.
    assert second.manifest["strategy_hash"] == first.manifest["strategy_hash"]
    assert second.manifest["run_hash"] != first.manifest["run_hash"]
    assert (
        second.manifest["signal_cache"]["plan_hash"]
        == first.manifest["signal_cache"]["plan_hash"]
    )
    assert second.result is not None
