"""Persistent signal-plan store tests.

The disk tier is what makes acceptance criterion 19 hold in the real path:
every API-triggered run executes in a new worker process.  These tests use a
temporary store root and a real subprocess, so no ``var/`` data and no sibling
data are touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.engine.signals import (
    DISK_FORMAT,
    SignalCache,
    SignalPlanStore,
    compute_signal_plan,
    make_signal_key,
    signal_spec_hash,
)

from .test_signal_cache import BAR, BAR_TYPE, TS0, bar_row, make_config

ROOT = Path(__file__).resolve().parents[2]


def make_plan(threshold: float = 0.1, *, data_version: str = "sha256:dv1", bar_type: str = BAR_TYPE):
    config = make_config(
        long_entry={"gt": ["close", threshold]},
        direction="long",
        settlement="rule_exit",
        bar_type=bar_type,
    )
    bars = [bar_row(TS0 + index * BAR, close) for index, close in enumerate([0.4, 0.6])]
    return compute_signal_plan(
        config,
        bars,
        lambda _ts: {"phase": "in_play", "time_to_kickoff_minutes": -5.0},
        data_version=data_version,
    )


def fresh_cache(root: Path, *, capacity: int = 4) -> SignalCache:
    """A cache with an empty memory LRU, as a new worker process would have."""
    return SignalCache(capacity=capacity, store=SignalPlanStore(root=root))


# ---------------------------------------------------------------------------
# Round trip


def test_disk_round_trip_reconstructs_the_plan_exactly(tmp_path):
    plan = make_plan()
    store = SignalPlanStore(root=tmp_path)
    assert store.save(plan.key, plan) is not None

    loaded = SignalPlanStore(root=tmp_path).load(plan.key)
    assert loaded is not None
    assert loaded.key == plan.key
    assert loaded.plan_hash == plan.plan_hash
    assert loaded.lifecycle_version == plan.lifecycle_version
    assert loaded.warmup_bars == plan.warmup_bars
    assert loaded.true_counts == plan.true_counts
    assert loaded.lifecycle_missing_bars == plan.lifecycle_missing_bars
    assert len(loaded) == len(plan)
    for expected, actual in zip(plan.rows, loaded.rows):
        assert actual.ts_event_ns == expected.ts_event_ns
        assert actual.close == expected.close
        assert actual.long_entry_ok == expected.long_entry_ok
        assert actual.long_exit_ok == expected.long_exit_ok
        assert actual.short_entry_ok == expected.short_entry_ok
        assert actual.short_exit_ok == expected.short_exit_ok
        assert {k: dict(v) for k, v in actual.indicator_values.items()} == {
            k: dict(v) for k, v in expected.indicator_values.items()
        }
        assert dict(actual.lifecycle) == dict(expected.lifecycle)

    files = sorted(path.suffix for path in tmp_path.iterdir())
    assert files == [".json", ".parquet"]


def test_memory_then_disk_hit_source(tmp_path):
    plan = make_plan()
    cache = fresh_cache(tmp_path)
    first = cache.get_or_compute(plan.key, lambda: plan)
    assert first.hit is False and first.source == "none"

    # The same cache answers from its memory LRU on the next lookup.
    same = cache.get_or_compute(
        plan.key, lambda: pytest.fail("memory must answer the second lookup")
    )
    assert same.hit is True and same.source == "memory"

    # A new process-like cache has an empty memory LRU but the same disk.
    reloaded = fresh_cache(tmp_path).get_or_compute(
        plan.key, lambda: pytest.fail("the plan must come from disk")
    )
    assert reloaded.hit is True
    assert reloaded.source == "disk"
    assert reloaded.plan.plan_hash == plan.plan_hash


def test_a_real_subprocess_loads_the_persisted_plan(tmp_path):
    """Evidence for the worker path: another process reads the same plan."""
    plan = make_plan()
    SignalPlanStore(root=tmp_path).save(plan.key, plan)

    script = """
import json, sys
from pathlib import Path
from backend.engine.signals import SignalPlanStore, make_signal_key

root, raw_key = sys.argv[1], json.loads(sys.argv[2])
plan = SignalPlanStore(root=Path(root)).load(make_signal_key(**raw_key))
if plan is None:
    print(json.dumps({"loaded": False}))
else:
    print(json.dumps({
        "loaded": True,
        "plan_hash": plan.plan_hash,
        "rows": len(plan),
        "warmup_bars": plan.warmup_bars,
        "spec_hash": plan.spec_hash,
    }, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), json.dumps(dict(plan.key._asdict()))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "loaded": True,
        "plan_hash": plan.plan_hash,
        "rows": 2,
        "warmup_bars": 0,
        "spec_hash": plan.spec_hash,
    }


# ---------------------------------------------------------------------------
# Misses and invalidation


def test_other_strategy_params_or_data_version_miss(tmp_path):
    plan = make_plan(0.1)
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)

    other_strategy = make_plan(0.2)
    assert other_strategy.key != plan.key
    assert store.load(other_strategy.key) is None

    other_data = make_plan(0.1, data_version="sha256:dv2")
    assert other_data.key != plan.key
    assert store.load(other_data.key) is None

    other_bar_type = make_plan(0.1, bar_type=f"{BAR_TYPE}-5m")
    assert other_bar_type.key != plan.key
    assert store.load(other_bar_type.key) is None

    # Misses never disturb the stored entry.
    assert store.load(plan.key).plan_hash == plan.plan_hash


def test_lifecycle_version_mismatch_is_a_miss(tmp_path):
    config = make_config(
        long_entry={"gt": ["close", 0.1]}, direction="long", bar_type=BAR_TYPE
    )
    bars = [bar_row(TS0, 0.6)]
    plan = compute_signal_plan(
        config, bars, lambda _ts: {"phase": "in_play"}, data_version="sha256:dv1",
        lifecycle_version="sha256:life-a",
    )
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)

    assert store.load(plan.key, lifecycle_version="sha256:life-a") is not None
    assert store.load(plan.key, lifecycle_version="sha256:life-b") is None
    # Unknown lifecycle versions are accepted, never invented.
    assert store.load(plan.key, lifecycle_version=None) is not None


def test_corrupt_metadata_is_a_miss_and_is_removed(tmp_path):
    plan = make_plan()
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)
    meta = next(tmp_path.glob("*.json"))
    parquet = meta.with_suffix(".parquet")
    meta.write_text("{not json")

    assert SignalPlanStore(root=tmp_path).load(plan.key) is None
    assert not meta.exists()
    assert not parquet.exists()


def test_truncated_parquet_is_a_miss_and_is_removed(tmp_path):
    plan = make_plan()
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)
    parquet = next(tmp_path.glob("*.parquet"))
    parquet.write_bytes(b"PAR1 truncated")

    assert SignalPlanStore(root=tmp_path).load(plan.key) is None
    assert not parquet.exists()
    assert not parquet.with_suffix(".json").exists()


def test_content_hash_mismatch_is_a_miss(tmp_path):
    plan = make_plan()
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)
    meta = next(tmp_path.glob("*.json"))
    payload = json.loads(meta.read_text())
    payload["plan_hash"] = "sha256:" + "0" * 64
    meta.write_text(json.dumps(payload))

    assert SignalPlanStore(root=tmp_path).load(plan.key) is None
    assert not meta.exists()


def test_orphan_parquet_is_not_served(tmp_path):
    plan = make_plan()
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)
    meta = next(tmp_path.glob("*.json"))
    meta.unlink()

    assert SignalPlanStore(root=tmp_path).load(plan.key) is None
    assert not meta.with_suffix(".parquet").exists()


# ---------------------------------------------------------------------------
# Caps and eviction


def _set_mtime(path: Path, seconds: int) -> None:
    ns = int(seconds) * 1_000_000_000
    os.utime(path, ns=(ns, ns))


def test_eviction_removes_the_lru_file_not_the_newest(tmp_path):
    store = SignalPlanStore(root=tmp_path, capacity=2, byte_cap=None)
    plans = [make_plan(0.1), make_plan(0.2), make_plan(0.3)]

    def stem(plan) -> str:
        return signal_spec_hash(plan.key).split(":", 1)[1]

    for seconds, plan in enumerate(plans):
        store.save(plan.key, plan)
        # Control recency explicitly instead of relying on filesystem clocks.
        _set_mtime(tmp_path / f"{stem(plan)}.json", seconds)

    newest = make_plan(0.4)
    store.save(newest.key, newest)

    assert len(store) == 2
    assert store.load(newest.key).plan_hash == newest.plan_hash
    assert store.load(plans[2].key) is not None
    # The two oldest entries were evicted; the newest survived.
    assert not (tmp_path / f"{stem(plans[0])}.json").exists()
    assert not (tmp_path / f"{stem(plans[0])}.parquet").exists()
    assert not (tmp_path / f"{stem(plans[1])}.json").exists()
    assert (tmp_path / f"{stem(newest)}.json").exists()


def test_store_stats_report_entries_and_bytes(tmp_path):
    store = SignalPlanStore(root=tmp_path, capacity=8)
    plan = make_plan()
    store.save(plan.key, plan)
    stats = store.stats()
    assert stats["entries"] == 1
    assert stats["bytes"] > 0
    assert stats["capacity"] == 8
    assert stats["enabled"] is True
    assert stats["root"] == str(tmp_path)

    cache = SignalCache(store=store)
    first = cache.get_or_compute(plan.key, lambda: plan)
    hit = cache.get_or_compute(plan.key, lambda: pytest.fail("memory must answer"))
    assert (first.source, hit.source) == ("disk", "memory")
    cache_stats = cache.stats()
    assert cache_stats["hits"] == 2
    assert cache_stats["misses"] == 0
    assert cache_stats["memory"]["entries"] == 1
    assert cache_stats["disk"]["entries"] == 1
    assert cache_stats["disk"]["bytes"] > 0


# ---------------------------------------------------------------------------
# Key and metadata contents


def test_persisted_key_and_metadata_have_no_execution_fields(tmp_path):
    config = make_config(
        long_entry={"gt": ["close", 0.1]},
        direction="long",
        bar_type=BAR_TYPE,
    )
    bars = [bar_row(TS0, 0.6)]
    plan = compute_signal_plan(
        config, bars, lambda _ts: {"phase": "in_play"}, data_version="sha256:dv1"
    )
    store = SignalPlanStore(root=tmp_path)
    store.save(plan.key, plan)

    meta = next(tmp_path.glob("*.json"))
    assert meta.stem == signal_spec_hash(plan.key).split(":", 1)[1]
    payload = json.loads(meta.read_text())
    assert payload["format"] == DISK_FORMAT
    assert payload["key"] == dict(plan.key._asdict())
    assert set(payload["key"]) == {
        "data_version",
        "bar_type",
        "strategy_hash",
        "warmup_bars",
        "indicator_impl_version",
    }
    assert payload["spec_hash"] == signal_spec_hash(plan.key)
    assert payload["plan_hash"] == plan.plan_hash
    assert payload["rows"] == 1
    assert payload["created_at"].endswith("Z")

    serialized = json.dumps(payload)
    for forbidden in (
        "execution",
        "profile",
        "sizing",
        "slippage",
        "bankroll",
        "fee_bps",
        "evaluation",
    ):
        assert forbidden not in serialized


def test_signal_key_has_no_execution_component():
    key = make_signal_key(
        data_version="sha256:dv1",
        bar_type=BAR_TYPE,
        strategy_hash="sha256:" + "1" * 64,
        warmup_bars=0,
    )
    assert key._fields == (
        "data_version",
        "bar_type",
        "strategy_hash",
        "warmup_bars",
        "indicator_impl_version",
    )
