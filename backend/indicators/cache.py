"""Thread-safe LRU cache for computed indicator series.

Chart previews and backtests ask for the same series repeatedly.  A series is
a pure function of ``(data_version, bar_type, spec_hash, impl_version)`` —
where ``spec_hash`` is the canonical instance list and ``impl_version`` is the
adapter version in :mod:`backend.indicators.compute`.  A parameter change is
therefore a different key, never a mutation of an existing entry.

Cached values are immutable: every series is a ``MappingProxyType`` of output
keys to tuples.  Callers that need mutable lists (JSON responses) use
:func:`thaw_series` or :func:`backend.indicators.compute.series_to_json`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, Callable, NamedTuple, Sequence

from backend.indicators.compute import INDICATOR_IMPL_VERSION, spec_hash

DEFAULT_CAPACITY = 32

FrozenSeries = Mapping[str, Mapping[str, tuple[float | None, ...]]]


class CacheKey(NamedTuple):
    """Cache key; the field order is the documented key order."""

    data_version: str
    bar_type: str
    spec_hash: str
    indicator_impl_version: str


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"cache key {name} must be a non-empty string")
    return value


def make_key(
    data_version: str,
    bar_type: str,
    instances: Iterable[Mapping[str, Any]],
    indicator_impl_version: str = INDICATOR_IMPL_VERSION,
) -> CacheKey:
    """Build the cache key for a data version, bar type and instance list."""
    return CacheKey(
        data_version=_require_str("data_version", data_version),
        bar_type=_require_str("bar_type", bar_type),
        spec_hash=spec_hash(instances),
        indicator_impl_version=_require_str(
            "indicator_impl_version", indicator_impl_version
        ),
    )


def freeze_series(
    series: Mapping[str, Mapping[str, Sequence[float | None]]],
) -> FrozenSeries:
    """Deep-freeze a series dict so a cached value cannot be mutated."""
    frozen: dict[str, Mapping[str, tuple[float | None, ...]]] = {}
    for instance_id, by_output in series.items():
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("series instance id must be a non-empty string")
        columns: dict[str, tuple[float | None, ...]] = {}
        for key, values in by_output.items():
            if not isinstance(key, str) or not key:
                raise ValueError("series output key must be a non-empty string")
            if isinstance(values, (str, bytes, Mapping)):
                raise ValueError(f"series '{instance_id}.{key}' must be a sequence")
            columns[key] = tuple(
                None if value is None else float(value) for value in values
            )
        frozen[instance_id] = MappingProxyType(columns)
    return MappingProxyType(frozen)


def thaw_series(
    series: Mapping[str, Mapping[str, Sequence[float | None]]],
) -> dict[str, dict[str, list[float | None]]]:
    """Mutable ``list`` copy of a (frozen) series, values unchanged."""
    return {
        instance_id: {key: list(values) for key, values in by_output.items()}
        for instance_id, by_output in series.items()
    }


class IndicatorCache:
    """Bounded least-recently-used cache of frozen indicator series."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ValueError("cache capacity must be an integer")
        if capacity < 1:
            raise ValueError("cache capacity must be >= 1")
        self._capacity = capacity
        self._entries: OrderedDict[CacheKey, FrozenSeries] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    # -- reading ---------------------------------------------------------

    def get(self, key: CacheKey) -> FrozenSeries | None:
        """Return the cached series, or ``None``; counts a hit or a miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

    def get_or_compute(
        self,
        key: CacheKey,
        factory: Callable[[], Mapping[str, Mapping[str, Sequence[float | None]]]],
    ) -> FrozenSeries:
        """Return the cached series or compute, freeze and store it.

        ``factory`` runs without the lock held, so a slow computation never
        blocks readers; two concurrent misses may both compute and the later
        insert wins (the values are identical by construction).
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return entry
            self._misses += 1
        value = freeze_series(factory())
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:  # another thread won the race
                self._entries.move_to_end(key)
                return existing
            self._insert(key, value)
        return value

    # -- writing ---------------------------------------------------------

    def put(
        self,
        key: CacheKey,
        series: Mapping[str, Mapping[str, Sequence[float | None]]],
    ) -> FrozenSeries:
        """Store (a frozen copy of) ``series``, evicting the oldest entry."""
        value = freeze_series(series)
        with self._lock:
            self._insert(key, value)
        return value

    def _insert(self, key: CacheKey, value: FrozenSeries) -> None:
        # Caller holds the lock.
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry and reset hit/miss counters."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    # -- introspection ---------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._entries),
                "capacity": self._capacity,
            }

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._entries


#: Process-wide cache used by the API and the engine.
DEFAULT_CACHE = IndicatorCache()


def cache_stats() -> dict[str, int]:
    return DEFAULT_CACHE.stats()


def clear_cache() -> None:
    DEFAULT_CACHE.clear()


def get_or_compute(
    key: CacheKey,
    factory: Callable[[], Mapping[str, Mapping[str, Sequence[float | None]]]],
) -> FrozenSeries:
    """:meth:`IndicatorCache.get_or_compute` on :data:`DEFAULT_CACHE`."""
    return DEFAULT_CACHE.get_or_compute(key, factory)


__all__ = [
    "CacheKey",
    "DEFAULT_CACHE",
    "DEFAULT_CAPACITY",
    "FrozenSeries",
    "IndicatorCache",
    "cache_stats",
    "clear_cache",
    "freeze_series",
    "get_or_compute",
    "make_key",
    "thaw_series",
]
