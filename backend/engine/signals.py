"""Signal plans: the execution-independent part of a backtest.

A signal plan is the per-bar truth of every declared rule tree plus the
indicator snapshot that produced it.  It deliberately contains **no**
positions, orders, sizes, fees or profiles: those belong to the strategy state
machine and the execution model, and changing them must not invalidate the
plan.

A plan is a pure function of

    (data_version, bar_type, strategy_hash, warmup_bars,
     indicator_impl_version)

plus the indicator and lifecycle inputs.  That is the key in ``docs/BRIEF.md``
§8.6.  The instrument is not a separate key component because
``strategy_hash`` hashes the whole strategy section, which contains
``instrument_id``.  Execution, sizing and the evaluation window are excluded by
construction: they are not arguments to :func:`compute_signal_plan`.

``lifecycle_version`` is not in the key, because ``run_data_version()`` already
fingerprints the lifecycle and settlement parquet files.  It is stored on the
plan and checked on lookup: if a caller supplies a lifecycle version that
differs from the cached plan's, the entry is treated as a miss rather than
served with stale lifecycle-conditioned truths.

The cache is an in-process LRU, like the indicator cache.  Workers are separate
processes (AGENTS.md §8), so a plan is reused by callers in the same process
(tests, a sweep loop, the API process); cross-process reuse would need a
persisted plan store, which is not part of this module.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, NamedTuple

from backend.engine.rule_strategy import (
    ACTIONS,
    OperandResolver,
    _as_number,
    collect_cross_operands,
    evaluate_condition,
    normalise_indicator_values,
)
from backend.indicators.compute import INDICATOR_IMPL_VERSION, canonical_instances
from backend.indicators.registry import get_spec
from backend.rules.schema import RunConfig, strategy_hash as compute_strategy_hash

DEFAULT_CAPACITY = 32

_EMPTY = MappingProxyType({})

_TRUTH_FIELDS = {
    "long_entry": "long_entry_ok",
    "long_exit": "long_exit_ok",
    "short_entry": "short_entry_ok",
    "short_exit": "short_exit_ok",
}


# ---------------------------------------------------------------------------
# Key


class SignalKey(NamedTuple):
    """Cache key for a signal plan; field order is the documented key order."""

    data_version: str
    bar_type: str
    strategy_hash: str
    warmup_bars: int
    indicator_impl_version: str


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"signal cache key {name} must be a non-empty string")
    return value


def _require_warmup(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("signal cache key warmup_bars must be an integer")
    if value < 0:
        raise ValueError("signal cache key warmup_bars must be >= 0")
    return value


def make_signal_key(
    *,
    data_version: str,
    bar_type: str,
    strategy_hash: str,
    warmup_bars: int,
    indicator_impl_version: str = INDICATOR_IMPL_VERSION,
) -> SignalKey:
    """Build the canonical plan key (BRIEF §8.6, no execution fields)."""
    return SignalKey(
        data_version=_require_str("data_version", data_version),
        bar_type=_require_str("bar_type", bar_type),
        strategy_hash=_require_str("strategy_hash", strategy_hash),
        warmup_bars=_require_warmup(warmup_bars),
        indicator_impl_version=_require_str(
            "indicator_impl_version", indicator_impl_version
        ),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def signal_spec_hash(key: SignalKey) -> str:
    """Stable SHA-256 over the canonical key, usable as a filename or id."""
    payload = {
        "data_version": key.data_version,
        "bar_type": key.bar_type,
        "strategy_hash": key.strategy_hash,
        "warmup_bars": key.warmup_bars,
        "indicator_impl_version": key.indicator_impl_version,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Rows and plans


def _ts_event_ns(row: Mapping[str, Any]) -> int:
    value = row.get("ts_event_ns")
    if value is None:
        value = row.get("ts_event")
    if value is None:
        raise ValueError("bar is missing 'ts_event_ns'")
    return int(value)


def _bar_for_indicators(row: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical bar mapping, as the indicator adapter expects it."""
    bar = dict(row)
    bar["ts_event"] = _ts_event_ns(row)
    bar.setdefault("ts_init", bar["ts_event"])
    return bar


def _freeze(values: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    return MappingProxyType(
        {str(instance): MappingProxyType(dict(instance_values)) for instance, instance_values in values.items()}
    )


@dataclass(frozen=True)
class SignalRow:
    """One bar's condition truths and indicator snapshot.

    The booleans are the *rule truth*, not an action: the strategy still decides
    entries and exits from them using its position, direction and lifecycle
    filters.
    """

    ts_event_ns: int
    close: float | None
    long_entry_ok: bool
    long_exit_ok: bool
    short_entry_ok: bool
    short_exit_ok: bool
    indicator_values: Mapping[str, Mapping[str, Any]]
    lifecycle: Mapping[str, Any]
    index: int = 0

    def truth(self, action: str) -> bool:
        """Truth for one of ``ACTIONS``, mirroring ``evaluate_condition``."""
        try:
            return bool(getattr(self, _TRUTH_FIELDS[action]))
        except KeyError:
            raise ValueError(f"unknown signal action {action!r}") from None


@dataclass(frozen=True)
class SignalPlan:
    """Immutable per-bar sequence of condition truths and indicator snapshots."""

    data_version: str
    bar_type: str
    strategy_hash: str
    warmup_bars: int
    indicator_impl_version: str
    lifecycle_version: str | None
    rows: tuple[SignalRow, ...]
    true_counts: Mapping[str, int] = field(default_factory=dict)
    lifecycle_missing_bars: int = 0
    plan_hash: str = field(init=False, compare=False)
    _by_ts: Mapping[int, int] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        index: dict[int, int] = {}
        digest = hashlib.sha256()
        digest.update(
            _canonical_json(
                {
                    "data_version": self.data_version,
                    "bar_type": self.bar_type,
                    "strategy_hash": self.strategy_hash,
                    "warmup_bars": self.warmup_bars,
                    "indicator_impl_version": self.indicator_impl_version,
                    "lifecycle_version": self.lifecycle_version,
                }
            ).encode()
        )
        for position, row in enumerate(self.rows):
            if row.ts_event_ns in index:
                raise ValueError(
                    f"duplicate bar ts_event_ns in signal plan: {row.ts_event_ns}"
                )
            index[row.ts_event_ns] = position
            digest.update(
                _canonical_json(
                    {
                        "ts": row.ts_event_ns,
                        "close": row.close,
                        "long_entry": row.long_entry_ok,
                        "long_exit": row.long_exit_ok,
                        "short_entry": row.short_entry_ok,
                        "short_exit": row.short_exit_ok,
                        "indicators": {
                            instance: dict(values)
                            for instance, values in row.indicator_values.items()
                        },
                        "lifecycle": dict(row.lifecycle),
                    }
                ).encode()
            )
        object.__setattr__(self, "_by_ts", MappingProxyType(index))
        object.__setattr__(
            self, "plan_hash", "sha256:" + digest.hexdigest()
        )

    # -- key / metadata ---------------------------------------------------

    @property
    def key(self) -> SignalKey:
        return SignalKey(
            data_version=self.data_version,
            bar_type=self.bar_type,
            strategy_hash=self.strategy_hash,
            warmup_bars=self.warmup_bars,
            indicator_impl_version=self.indicator_impl_version,
        )

    @property
    def spec_hash(self) -> str:
        return signal_spec_hash(self.key)

    def index_of(self, ts_event_ns: int) -> int | None:
        return self._by_ts.get(int(ts_event_ns))

    def row_at(self, ts_event_ns: int) -> SignalRow | None:
        position = self.index_of(ts_event_ns)
        return None if position is None else self.rows[position]

    def __len__(self) -> int:
        return len(self.rows)

    def metadata(self) -> dict[str, Any]:
        """JSON-safe summary; never contains execution, sizing or fee fields."""
        return {
            "key": {
                "data_version": self.data_version,
                "bar_type": self.bar_type,
                "strategy_hash": self.strategy_hash,
                "warmup_bars": self.warmup_bars,
                "indicator_impl_version": self.indicator_impl_version,
            },
            "spec_hash": self.spec_hash,
            "lifecycle_version": self.lifecycle_version,
            "rows": len(self.rows),
            "warmup_bars": self.warmup_bars,
            "true_counts": dict(self.true_counts),
            "lifecycle_missing_bars": self.lifecycle_missing_bars,
            "plan_hash": self.plan_hash,
        }


# ---------------------------------------------------------------------------
# Computation


def declared_warmup(instances: Iterable[Mapping[str, Any] | Any]) -> int:
    """Maximum declared warm-up (leading nulls) across the indicator instances."""
    specs = canonical_instances(
        instance.model_dump(mode="json") if hasattr(instance, "model_dump") else instance
        for instance in instances
    )
    return max((int(get_spec(item["type"]).warmup(item["params"])) for item in specs), default=0)


class _NullIndicators:
    def update(self, _bar: Mapping[str, Any]) -> "_NullIndicators":
        return self

    def values(self) -> dict[str, dict[str, Any]]:
        return {}


def _default_indicator_factory(instances: Sequence[Any]) -> Any:
    from backend.indicators.compute import IndicatorSet  # noqa: PLC0415

    return IndicatorSet(instances)


def _resolve_lifecycle(
    lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None, ts_ns: int
) -> tuple[Mapping[str, Any], bool]:
    if lifecycle_lookup is None:
        return _EMPTY, False
    try:
        values = lifecycle_lookup(ts_ns)
    except Exception:  # noqa: BLE001 - lifecycle is advisory, never fatal
        values = None
    if not values:
        return _EMPTY, False
    return MappingProxyType(dict(values)), True


def compute_signal_plan(
    run_config: RunConfig,
    bars: Sequence[Mapping[str, Any]],
    lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None,
    indicator_factory: Callable[[Sequence[Any]], Any] | None = None,
    *,
    data_version: str,
    indicator_impl_version: str = INDICATOR_IMPL_VERSION,
    lifecycle_version: str | None = None,
    warmup_bars: int | None = None,
) -> SignalPlan:
    """Compute the condition-truth plan for one strategy over ``bars``.

    The evaluator is the same :func:`backend.engine.rule_strategy.evaluate_condition`
    the runtime path uses, with the same previous-bar map for ``crosses_*``, so
    plan and runtime decisions agree by construction.  ``evaluate_condition``
    returns ``False`` whenever a required operand is ``None``, which is what
    keeps warm-up bars false rather than falsely true.
    """
    section = run_config.strategy
    instances = [
        spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
        for spec in section.indicators
    ]
    factory = indicator_factory or _default_indicator_factory
    indicators = factory(instances) if instances else _NullIndicators()
    warmup = declared_warmup(section.indicators) if warmup_bars is None else int(warmup_bars)

    trees = {
        "long_entry": section.long_entry,
        "long_exit": section.long_exit,
        "short_entry": section.short_entry,
        "short_exit": section.short_exit,
    }
    cross_operands: set[str] = set()
    for tree in trees.values():
        collect_cross_operands(tree, cross_operands)

    counts = {action: 0 for action in ACTIONS}
    missing_lifecycle = 0
    previous: dict[str, Any] = {}
    rows: list[SignalRow] = []

    for position, raw in enumerate(bars):
        if not isinstance(raw, Mapping):
            raise ValueError(f"bars[{position}] must be a mapping")
        bar = _bar_for_indicators(raw)
        ts_ns = int(bar["ts_event"])

        result = indicators.update(bar)
        if result is None:
            result = indicators
        indicator_values = normalise_indicator_values(result)

        lifecycle, present = _resolve_lifecycle(lifecycle_lookup, ts_ns)
        if not present:
            missing_lifecycle += 1
        resolver = OperandResolver(bar, indicator_values, lifecycle)

        truths: dict[str, bool] = {}
        for action, tree in trees.items():
            truth = bool(
                tree is not None and evaluate_condition(tree, resolver, previous)
            )
            truths[action] = truth
            if truth:
                counts[action] += 1

        # Cross operands for the next bar: the previous-bar values are exactly
        # the resolver values of this bar, as in ``RuleStrategy.on_bar``.
        previous = {operand: resolver.value(operand) for operand in cross_operands}

        rows.append(
            SignalRow(
                ts_event_ns=ts_ns,
                close=_as_number(bar.get("close")),
                long_entry_ok=truths["long_entry"],
                long_exit_ok=truths["long_exit"],
                short_entry_ok=truths["short_entry"],
                short_exit_ok=truths["short_exit"],
                indicator_values=_freeze(indicator_values),
                lifecycle=lifecycle,
                index=position,
            )
        )

    return SignalPlan(
        data_version=_require_str("data_version", data_version),
        bar_type=_require_str("bar_type", section.bar_type),
        strategy_hash=compute_strategy_hash(run_config),
        warmup_bars=_require_warmup(warmup),
        indicator_impl_version=_require_str(
            "indicator_impl_version", indicator_impl_version
        ),
        lifecycle_version=lifecycle_version,
        rows=tuple(rows),
        true_counts=MappingProxyType(dict(counts)),
        lifecycle_missing_bars=missing_lifecycle,
    )


def signal_plan_for_config(
    run_config: RunConfig,
    bars: Sequence[Mapping[str, Any]],
    lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None,
    *,
    data_version: str,
    lifecycle_version: str | None = None,
    cache: "SignalCache | None" = None,
    indicator_factory: Callable[[Sequence[Any]], Any] | None = None,
) -> tuple[SignalPlan, bool]:
    """Return ``(plan, hit)`` for a run, reusing the default cache.

    This is the runner's entry point: the key is derived from the run config
    and the data version only, so two runs that differ in execution, sizing or
    evaluation share one plan.
    """
    key = make_signal_key(
        data_version=data_version,
        bar_type=run_config.strategy.bar_type,
        strategy_hash=compute_strategy_hash(run_config),
        warmup_bars=declared_warmup(run_config.strategy.indicators),
    )
    store = cache if cache is not None else DEFAULT_SIGNAL_CACHE

    def build() -> SignalPlan:
        return compute_signal_plan(
            run_config,
            bars,
            lifecycle_lookup,
            indicator_factory,
            data_version=data_version,
            lifecycle_version=lifecycle_version,
            warmup_bars=key.warmup_bars,
        )

    return store.get_or_compute(key, build, lifecycle_version=lifecycle_version)


# ---------------------------------------------------------------------------
# Cache


class SignalCache:
    """Bounded least-recently-used cache of immutable signal plans."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ValueError("signal cache capacity must be an integer")
        if capacity < 1:
            raise ValueError("signal cache capacity must be >= 1")
        self._capacity = capacity
        self._entries: OrderedDict[SignalKey, SignalPlan] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    # -- reading ---------------------------------------------------------

    def get(
        self, key: SignalKey, *, lifecycle_version: str | None = None
    ) -> SignalPlan | None:
        """Return the cached plan or ``None``; counts a hit or a miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if not _lifecycle_matches(entry, lifecycle_version):
                # A plan conditioned on other lifecycle data is not this run's
                # plan: miss rather than serve stale phase/time truths.
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

    def get_or_compute(
        self,
        key: SignalKey,
        factory: Callable[[], SignalPlan],
        *,
        lifecycle_version: str | None = None,
    ) -> tuple[SignalPlan, bool]:
        """Return ``(plan, hit)``, computing and storing on a miss.

        ``factory`` runs without the lock held so a slow plan never blocks
        readers; two concurrent misses may both compute and the later insert
        wins (the plans are identical by construction).
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and _lifecycle_matches(entry, lifecycle_version):
                self._entries.move_to_end(key)
                self._hits += 1
                return entry, True
            self._misses += 1
        plan = factory()
        if not isinstance(plan, SignalPlan):
            raise TypeError("signal plan factory must return a SignalPlan")
        if plan.key != key:
            raise ValueError(
                "computed signal plan key does not match the requested key"
            )
        value = self.put(key, plan)
        with self._lock:
            # Another thread may have inserted an identical plan meanwhile.
            entry = self._entries.get(key)
            return (entry if entry is not None else value), False

    # -- writing ---------------------------------------------------------

    def put(self, key: SignalKey, plan: SignalPlan) -> SignalPlan:
        if not isinstance(plan, SignalPlan):
            raise TypeError("signal cache stores SignalPlan values")
        if plan.key != key:
            raise ValueError("signal plan key does not match the cache key")
        with self._lock:
            self._entries[key] = plan
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return plan

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


def _lifecycle_matches(plan: SignalPlan, expected: str | None) -> bool:
    """True when the caller cannot prove the cached plan is from other data.

    ``None`` means "not known here" and is accepted; a known mismatch is not.
    """
    if expected is None or plan.lifecycle_version is None:
        return True
    return plan.lifecycle_version == expected


DEFAULT_SIGNAL_CACHE = SignalCache()


def cache_stats() -> dict[str, int]:
    return DEFAULT_SIGNAL_CACHE.stats()


def clear_cache() -> None:
    DEFAULT_SIGNAL_CACHE.clear()


__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_SIGNAL_CACHE",
    "SignalCache",
    "SignalKey",
    "SignalPlan",
    "SignalRow",
    "cache_stats",
    "clear_cache",
    "compute_signal_plan",
    "declared_warmup",
    "make_signal_key",
    "signal_plan_for_config",
    "signal_spec_hash",
]
