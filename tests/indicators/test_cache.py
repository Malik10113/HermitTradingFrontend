"""Cache behaviour: keys, LRU eviction, immutability, stats, threads."""

from __future__ import annotations

import threading
import time

import pytest

from backend.indicators.cache import (
    CacheKey,
    IndicatorCache,
    cache_stats,
    clear_cache,
    freeze_series,
    get_or_compute,
    make_key,
    thaw_series,
)
from backend.indicators.compute import compute_series, spec_hash
from tests.indicators.fixtures.bars import make_bars

BARS = make_bars(20)
SMA3 = [{"instance_id": "sma3", "type": "sma", "params": {"period": 3}}]


def _series(instances=None):
    return compute_series(BARS, instances or SMA3)


def test_make_key_uses_documented_fields() -> None:
    key = make_key("sha256:data", "1m", SMA3)
    assert isinstance(key, CacheKey)
    assert tuple(key) == (
        "sha256:data",
        "1m",
        spec_hash(SMA3),
        key.indicator_impl_version,
    )
    assert key.spec_hash.startswith("sha256:")


@pytest.mark.parametrize(
    ("data_version", "bar_type"),
    [("", "1m"), ("sha256:data", "")],
)
def test_make_key_rejects_blank_parts(data_version: str, bar_type: str) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        make_key(data_version, bar_type, SMA3)


def test_parameter_change_produces_a_new_key_and_identical_specs_reuse_it() -> None:
    same = make_key("v1", "1m", SMA3)
    reordered = make_key("v1", "1m", list(reversed(SMA3)))
    changed = make_key(
        "v1", "1m", [{"instance_id": "sma3", "type": "sma", "params": {"period": 4}}]
    )
    other_bar_type = make_key("v1", "5m", SMA3)
    other_data = make_key("v2", "1m", SMA3)

    assert same == reordered
    assert same != changed
    assert same != other_bar_type
    assert same != other_data


def test_hits_and_misses_are_counted() -> None:
    cache = IndicatorCache(capacity=4)
    key = make_key("v1", "1m", SMA3)
    assert cache.stats() == {"hits": 0, "misses": 0, "size": 0, "capacity": 4}

    series = _series()
    cache.put(key, series)
    assert cache.get(key) is not None
    assert cache.get(key) is not None
    assert cache.get(make_key("v2", "1m", SMA3)) is None

    assert cache.stats() == {"hits": 2, "misses": 1, "size": 1, "capacity": 4}
    assert len(cache) == 1
    assert key in cache


def test_cached_series_is_immutable() -> None:
    cache = IndicatorCache()
    key = make_key("v1", "1m", SMA3)
    cache.put(key, _series())

    entry = cache.get(key)
    assert entry is not None
    with pytest.raises(TypeError):
        entry["sma3"]["value"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        entry["sma3"]["value"][0] = 1.0  # type: ignore[index]
    assert isinstance(entry["sma3"]["value"], tuple)
    assert entry["sma3"]["value"][:2] == (None, None)


def test_stored_copy_is_not_affected_by_caller_mutation() -> None:
    cache = IndicatorCache()
    key = make_key("v1", "1m", SMA3)
    series = _series()
    cache.put(key, series)

    series["sma3"]["value"][0] = 999.0
    series["sma3"]["value"].append(1.0)
    series["sma3"]["extra"] = [0.0]

    entry = cache.get(key)
    assert entry is not None
    assert entry["sma3"]["value"][0] is None
    assert len(entry["sma3"]["value"]) == len(BARS)
    assert "extra" not in entry["sma3"]


def test_equivalent_specs_share_one_computation() -> None:
    cache = IndicatorCache()
    calls: list[int] = []

    def factory():
        calls.append(1)
        return _series()

    first = cache.get_or_compute(make_key("v1", "1m", SMA3), factory)
    # Same spec, list order reversed: still the same cache entry.
    second = cache.get_or_compute(make_key("v1", "1m", list(reversed(SMA3))), factory)
    # A parameter change is a new entry, not a mutation.
    changed = [{"instance_id": "sma3", "type": "sma", "params": {"period": 4}}]
    third = cache.get_or_compute(make_key("v1", "1m", changed), lambda: _series(changed))

    assert first is second
    assert third is not first
    assert len(calls) == 1
    assert cache.stats()["size"] == 2


def test_lru_eviction_drops_the_least_recently_used_entry() -> None:
    cache = IndicatorCache(capacity=2)
    key_a = make_key("v1", "1m", [{"instance_id": "a", "type": "sma", "params": {"period": 2}}])
    key_b = make_key("v1", "1m", [{"instance_id": "b", "type": "sma", "params": {"period": 3}}])
    key_c = make_key("v1", "1m", [{"instance_id": "c", "type": "sma", "params": {"period": 4}}])

    cache.put(key_a, {"a": {"value": [1.0]}})
    cache.put(key_b, {"b": {"value": [2.0]}})
    assert cache.get(key_a) is not None  # A becomes most recently used

    cache.put(key_c, {"c": {"value": [3.0]}})
    assert cache.get(key_a) is not None
    assert cache.get(key_b) is None
    assert cache.get(key_c) is not None
    assert cache.stats()["size"] == 2


def test_get_or_compute_caches_and_counts_once() -> None:
    cache = IndicatorCache()
    key = make_key("v1", "1m", SMA3)
    calls: list[int] = []

    def factory():
        calls.append(1)
        return _series()

    first = cache.get_or_compute(key, factory)
    second = cache.get_or_compute(key, factory)

    assert first is second
    assert len(calls) == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_get_or_compute_does_not_cache_failures() -> None:
    cache = IndicatorCache()
    key = make_key("v1", "1m", SMA3)
    calls: list[int] = []

    def failing():
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        cache.get_or_compute(key, failing)

    assert cache.get(key) is None
    assert cache.stats()["misses"] == 2  # the failed compute plus the get above
    assert cache.stats()["size"] == 0

    cache.get_or_compute(key, lambda: _series())
    assert cache.stats()["size"] == 1
    assert len(calls) == 1


def test_clear_resets_entries_and_counters() -> None:
    cache = IndicatorCache(capacity=2)
    key = make_key("v1", "1m", SMA3)
    cache.put(key, _series())
    cache.get(key)

    cache.clear()
    assert cache.stats() == {"hits": 0, "misses": 0, "size": 0, "capacity": 2}
    assert cache.get(key) is None


def test_capacity_is_validated() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError, match=">= 1"):
            IndicatorCache(capacity=bad)
    with pytest.raises(ValueError, match="integer"):
        IndicatorCache(capacity=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="integer"):
        IndicatorCache(capacity=True)  # type: ignore[arg-type]


def test_freeze_and_thaw_round_trip() -> None:
    series = _series()
    frozen = freeze_series(series)
    assert frozen["sma3"]["value"] == tuple(series["sma3"]["value"])
    assert thaw_series(frozen) == {
        instance_id: {key: list(values) for key, values in by_output.items()}
        for instance_id, by_output in series.items()
    }


def test_freeze_rejects_non_sequences() -> None:
    with pytest.raises(ValueError, match="must be a sequence"):
        freeze_series({"x": {"value": "not-a-series"}})


def test_concurrent_get_or_compute_is_thread_safe() -> None:
    cache = IndicatorCache()
    key = make_key("v1", "1m", SMA3)
    call_lock = threading.Lock()
    calls = 0
    results: list[object] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def factory():
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.005)
        return _series()

    def worker():
        try:
            start.wait(timeout=5)
            results.append(cache.get_or_compute(key, factory))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(results) == 8
    assert all(result is results[0] for result in results)
    assert 1 <= calls <= 8
    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["hits"] + stats["misses"] == 8


def test_module_level_cache_helpers_delegate_to_default_cache() -> None:
    clear_cache()
    assert cache_stats()["size"] == 0
    key = make_key("v1", "1m", SMA3)
    get_or_compute(key, lambda: _series())
    assert cache_stats()["size"] == 1
    clear_cache()
    assert cache_stats() == {"hits": 0, "misses": 0, "size": 0, "capacity": 32}
