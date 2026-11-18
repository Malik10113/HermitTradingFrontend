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

The cache is two-tier: a process LRU and a persistent store under
``var/cache/signals/``.  Workers are separate processes (AGENTS.md §8), so the
disk tier is what makes acceptance criterion 19 hold in the real path: the
first worker writes the plan, the next worker (same strategy, different
execution profile) reads it instead of recomputing.  Disk entries are one
parquet file plus one JSON metadata file per ``signal_spec_hash(key)``; the
metadata is the commit marker and a malformed or mismatched entry is deleted,
never served.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq

from backend import config
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
DEFAULT_DISK_CAPACITY = 128
DEFAULT_DISK_BYTE_CAP = 256 * 1024 * 1024
DISK_FORMAT = "htf-signal-plan/1"
_ORPHAN_GRACE_NS = 60 * 60 * 1_000_000_000  # 1 hour

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


class CacheHit(NamedTuple):
    """Outcome of a cache lookup: the plan, whether it was a hit, and where."""

    plan: SignalPlan
    hit: bool
    source: str  # "memory" | "disk" | "none"


def signal_plan_for_config(
    run_config: RunConfig,
    bars: Sequence[Mapping[str, Any]],
    lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None,
    *,
    data_version: str,
    lifecycle_version: str | None = None,
    cache: "SignalCache | None" = None,
    indicator_factory: Callable[[Sequence[Any]], Any] | None = None,
) -> CacheHit:
    """Return the plan plus ``(hit, source)`` for a run.

    This is the runner's entry point: the key is derived from the run config
    and the data version only, so two runs that differ in execution, sizing or
    evaluation share one plan.  The default cache is two-tier, so a worker
    process reads a plan written by an earlier worker.
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
# Persistent store
#
# One parquet file (rows) plus one JSON file (key + provenance) per
# ``signal_spec_hash(key)``.  The JSON is written last and is the commit
# marker: a parquet without metadata, a metadata without parquet, or any file
# that fails validation is a miss and is deleted.  Writes are temp-file +
# ``os.replace`` so a reader (or a crash) never sees a half-written plan.

_PLAN_SCHEMA = pa.schema(
    [
        pa.field("ts_event_ns", pa.int64(), nullable=False),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("long_entry_ok", pa.bool_(), nullable=False),
        pa.field("long_exit_ok", pa.bool_(), nullable=False),
        pa.field("short_entry_ok", pa.bool_(), nullable=False),
        pa.field("short_exit_ok", pa.bool_(), nullable=False),
        pa.field("indicator_values", pa.string(), nullable=False),
        pa.field("lifecycle", pa.string(), nullable=False),
    ]
)


def _temp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def _row_record(row: SignalRow) -> dict[str, Any]:
    return {
        "ts_event_ns": int(row.ts_event_ns),
        "close": row.close,
        "long_entry_ok": bool(row.long_entry_ok),
        "long_exit_ok": bool(row.long_exit_ok),
        "short_entry_ok": bool(row.short_entry_ok),
        "short_exit_ok": bool(row.short_exit_ok),
        "indicator_values": json.dumps(
            {instance: dict(values) for instance, values in row.indicator_values.items()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        "lifecycle": json.dumps(
            dict(row.lifecycle),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ),
    }


def _row_from_record(record: Mapping[str, Any], index: int) -> SignalRow:
    indicators = json.loads(record["indicator_values"] or "{}")
    if not isinstance(indicators, Mapping):
        raise ValueError("signal plan row has malformed indicator_values")
    frozen: dict[str, dict[str, Any]] = {}
    for instance, values in indicators.items():
        if not isinstance(values, Mapping):
            raise ValueError("signal plan row has malformed indicator values")
        frozen[str(instance)] = dict(values)
    lifecycle = json.loads(record["lifecycle"] or "{}")
    if not isinstance(lifecycle, Mapping):
        raise ValueError("signal plan row has malformed lifecycle")
    close = record.get("close")
    return SignalRow(
        ts_event_ns=int(record["ts_event_ns"]),
        close=None if close is None else float(close),
        long_entry_ok=bool(record["long_entry_ok"]),
        long_exit_ok=bool(record["long_exit_ok"]),
        short_entry_ok=bool(record["short_entry_ok"]),
        short_exit_ok=bool(record["short_exit_ok"]),
        indicator_values=_freeze(frozen),
        lifecycle=MappingProxyType(dict(lifecycle)),
        index=index,
    )


def _lifecycle_matches(plan: SignalPlan, expected: str | None) -> bool:
    """True when the caller cannot prove the cached plan is from other data.

    ``None`` means "not known here" and is accepted; a known mismatch is not.
    """
    if expected is None or plan.lifecycle_version is None:
        return True
    return plan.lifecycle_version == expected


def _metadata_lifecycle_matches(metadata: Any, expected: str | None) -> bool:
    """Cheap pre-check so a lifecycle mismatch is a miss, not corruption."""
    if expected is None or not isinstance(metadata, Mapping):
        return True
    stored = metadata.get("lifecycle_version")
    if stored is None:
        return True
    return stored == expected


class SignalPlanStore:
    """Persistent, atomically written signal plans.

    Location: ``config.SIGNAL_CACHE_DIR`` (``var/cache/signals/``).  Eviction
    policy: at most ``capacity`` plans (default 128) **and** at most
    ``byte_cap`` bytes (default 256 MiB; ``None`` disables the byte cap).
    Least-recently-used entries are removed first, ordered by the metadata
    file's mtime, which is refreshed on every load.  The most recent entry is
    never evicted, so a single plan larger than the byte cap is still cached.
    A plan being read or written is protected by the store lock; cross-process
    writers only ever publish via atomic rename.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        capacity: int = DEFAULT_DISK_CAPACITY,
        byte_cap: int | None = DEFAULT_DISK_BYTE_CAP,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ValueError("signal store capacity must be an integer")
        if capacity < 1:
            raise ValueError("signal store capacity must be >= 1")
        if byte_cap is not None:
            if isinstance(byte_cap, bool) or not isinstance(byte_cap, int):
                raise ValueError("signal store byte_cap must be an integer or None")
            if byte_cap < 1:
                raise ValueError("signal store byte_cap must be >= 1 or None")
        self._root = Path(root) if root is not None else config.SIGNAL_CACHE_DIR
        self._capacity = capacity
        self._byte_cap = byte_cap
        self._lock = threading.RLock()
        self._disk_hits = 0
        self._misses = 0
        self._write_errors = 0

    # -- paths -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    def _stem(self, key: SignalKey) -> str:
        # Drop the "sha256:" prefix; the full hash stays in the metadata.
        return signal_spec_hash(key).split(":", 1)[1]

    def _paths(self, key: SignalKey) -> tuple[Path, Path]:
        stem = self._stem(key)
        return self._root / f"{stem}.parquet", self._root / f"{stem}.json"

    # -- reading ---------------------------------------------------------

    def load(
        self, key: SignalKey, *, lifecycle_version: str | None = None
    ) -> SignalPlan | None:
        """Return the persisted plan, or ``None`` for a miss.

        A known lifecycle-version mismatch is a miss but leaves the entry in
        place (the plan is still valid for the data version it was written
        under).  Malformed metadata, a partial pair, an unreadable parquet or a
        content-hash mismatch is corruption: the entry is deleted, never served.
        """
        parquet_path, meta_path = self._paths(key)
        with self._lock:
            if not meta_path.exists() and not parquet_path.exists():
                self._misses += 1
                return None
            try:
                if not (meta_path.exists() and parquet_path.exists()):
                    raise ValueError("partial signal plan entry")
                metadata = json.loads(meta_path.read_text())
            except Exception:  # noqa: BLE001 - corrupt entries are misses
                self._discard(parquet_path, meta_path)
                self._misses += 1
                return None
            if not _metadata_lifecycle_matches(metadata, lifecycle_version):
                self._misses += 1
                return None
            try:
                plan = self._plan_from_metadata(
                    key, metadata, parquet_path, lifecycle_version
                )
            except Exception:  # noqa: BLE001 - corrupt entries are misses
                self._discard(parquet_path, meta_path)
                self._misses += 1
                return None
            try:
                os.utime(meta_path, None)  # recency for LRU eviction
            except OSError:
                pass
            self._disk_hits += 1
            return plan

    def _plan_from_metadata(
        self,
        key: SignalKey,
        metadata: Mapping[str, Any],
        parquet_path: Path,
        lifecycle_version: str | None,
    ) -> SignalPlan:
        if not isinstance(metadata, Mapping):
            raise ValueError("signal plan metadata must be an object")
        if metadata.get("format") != DISK_FORMAT:
            raise ValueError("unknown signal plan format")
        if metadata.get("spec_hash") != signal_spec_hash(key):
            raise ValueError("signal plan spec hash mismatch")
        raw_key = metadata.get("key")
        if not isinstance(raw_key, Mapping):
            raise ValueError("signal plan metadata has no key")
        if SignalKey(**dict(raw_key)) != key:
            raise ValueError("signal plan key mismatch")
        stored_lifecycle = metadata.get("lifecycle_version")
        if stored_lifecycle is not None and not isinstance(stored_lifecycle, str):
            raise ValueError("signal plan lifecycle_version must be a string or null")
        if (
            lifecycle_version is not None
            and stored_lifecycle is not None
            and lifecycle_version != stored_lifecycle
        ):
            raise ValueError("signal plan lifecycle_version mismatch")
        plan_hash = metadata.get("plan_hash")
        if not isinstance(plan_hash, str) or not plan_hash.startswith("sha256:"):
            raise ValueError("signal plan metadata has no plan hash")
        rows_expected = int(metadata["rows"])
        if rows_expected < 0:
            raise ValueError("signal plan row count is negative")
        true_counts = metadata.get("true_counts") or {}
        if not isinstance(true_counts, Mapping):
            raise ValueError("signal plan true_counts must be an object")
        missing = int(metadata.get("lifecycle_missing_bars") or 0)

        table = pq.read_table(parquet_path)
        if table.num_rows != rows_expected:
            raise ValueError("signal plan row count does not match its metadata")
        missing_columns = set(_PLAN_SCHEMA.names) - set(table.column_names)
        if missing_columns:
            raise ValueError(
                f"signal plan parquet is missing columns {sorted(missing_columns)}"
            )
        rows = tuple(
            _row_from_record(record, index)
            for index, record in enumerate(table.to_pylist())
        )
        plan = SignalPlan(
            data_version=key.data_version,
            bar_type=key.bar_type,
            strategy_hash=key.strategy_hash,
            warmup_bars=key.warmup_bars,
            indicator_impl_version=key.indicator_impl_version,
            lifecycle_version=stored_lifecycle,
            rows=rows,
            true_counts=MappingProxyType(
                {str(name): int(count) for name, count in true_counts.items()}
            ),
            lifecycle_missing_bars=missing,
        )
        if plan.plan_hash != plan_hash:
            raise ValueError("signal plan content hash mismatch")
        if dict(plan.true_counts) != {
            str(name): int(count) for name, count in true_counts.items()
        }:
            raise ValueError("signal plan true_counts mismatch")
        return plan

    # -- writing ---------------------------------------------------------

    def save(self, key: SignalKey, plan: SignalPlan) -> Path | None:
        """Persist ``plan`` atomically; returns the metadata path or ``None``.

        Failures are swallowed into ``write_errors``: a cache write must never
        fail a backtest, and a half-written entry is removed rather than served.
        """
        if not isinstance(plan, SignalPlan):
            raise TypeError("signal store writes SignalPlan values")
        if plan.key != key:
            raise ValueError("signal plan key does not match the store key")
        parquet_path, meta_path = self._paths(key)
        metadata = {
            "format": DISK_FORMAT,
            "key": dict(key._asdict()),
            "spec_hash": signal_spec_hash(key),
            "plan_hash": plan.plan_hash,
            "lifecycle_version": plan.lifecycle_version,
            "rows": len(plan.rows),
            "warmup_bars": plan.warmup_bars,
            "true_counts": dict(plan.true_counts),
            "lifecycle_missing_bars": plan.lifecycle_missing_bars,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with self._lock:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
                if plan.rows:
                    table = pa.Table.from_pylist(
                        [_row_record(row) for row in plan.rows], schema=_PLAN_SCHEMA
                    )
                else:
                    table = _PLAN_SCHEMA.empty_table()
                tmp_parquet = _temp_path(parquet_path)
                try:
                    pq.write_table(table, tmp_parquet)
                    os.replace(tmp_parquet, parquet_path)
                finally:
                    tmp_parquet.unlink(missing_ok=True)
                # Metadata last: its presence is the commit marker.
                tmp_meta = _temp_path(meta_path)
                try:
                    tmp_meta.write_text(json.dumps(metadata, indent=2, sort_keys=True))
                    os.replace(tmp_meta, meta_path)
                finally:
                    tmp_meta.unlink(missing_ok=True)
                self._evict(keep_stem=parquet_path.stem)
                return meta_path
            except Exception:  # noqa: BLE001 - cache writes are best effort
                self._write_errors += 1
                self._discard(parquet_path, meta_path)
                return None

    def _evict(self, *, keep_stem: str | None = None) -> None:
        # Caller holds the lock.  Ordering is by metadata mtime (recency), with
        # the stem as a deterministic tie-break.
        entries: list[dict[str, Any]] = []
        for meta_path in self._root.glob("*.json"):
            try:
                stat = meta_path.stat()
            except OSError:
                continue
            parquet_path = meta_path.with_suffix(".parquet")
            size = stat.st_size
            if parquet_path.exists():
                try:
                    size += parquet_path.stat().st_size
                except OSError:
                    pass
            entries.append(
                {
                    "mtime_ns": stat.st_mtime_ns,
                    "stem": meta_path.stem,
                    "meta": meta_path,
                    "parquet": parquet_path,
                    "bytes": size,
                }
            )
        entries.sort(key=lambda entry: (entry["mtime_ns"], entry["stem"]))
        self._remove_orphans({entry["stem"] for entry in entries})

        total_bytes = sum(entry["bytes"] for entry in entries)
        count = len(entries)
        candidates = [entry for entry in entries if entry["stem"] != keep_stem]
        while (
            count > self._capacity
            or (self._byte_cap is not None and total_bytes > self._byte_cap)
        ) and candidates:
            victim = candidates.pop(0)
            try:
                victim["meta"].unlink(missing_ok=True)
                victim["parquet"].unlink(missing_ok=True)
            except OSError:
                continue
            total_bytes -= victim["bytes"]
            count -= 1

    def _remove_orphans(self, known_stems: set[str]) -> None:
        # A parquet without metadata is a crashed write.  Only old orphans are
        # removed so a concurrent writer's parquet is never deleted between
        # its rename and its metadata commit.
        now_ns = datetime.now(timezone.utc).timestamp() * 1_000_000_000
        for parquet_path in self._root.glob("*.parquet"):
            if parquet_path.stem in known_stems:
                continue
            try:
                if now_ns - parquet_path.stat().st_mtime_ns < _ORPHAN_GRACE_NS:
                    continue
                parquet_path.unlink()
            except OSError:
                continue

    def _discard(self, parquet_path: Path, meta_path: Path) -> None:
        # Caller holds the lock.
        try:
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            parquet_path.unlink(missing_ok=True)
        except OSError:
            pass

    def discard(self, key: SignalKey) -> None:
        parquet_path, meta_path = self._paths(key)
        with self._lock:
            self._discard(parquet_path, meta_path)

    # -- maintenance / introspection ------------------------------------

    def clear(self) -> None:
        """Delete every persisted plan and reset the store counters."""
        with self._lock:
            if self._root.exists():
                for path in list(self._root.glob("*.json")) + list(
                    self._root.glob("*.parquet")
                ):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                for path in list(self._root.glob(".*.tmp")):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._disk_hits = 0
            self._misses = 0
            self._write_errors = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            metas = list(self._root.glob("*.json")) if self._root.exists() else []
            total_bytes = 0
            for meta_path in metas:
                try:
                    total_bytes += meta_path.stat().st_size
                except OSError:
                    continue
                parquet_path = meta_path.with_suffix(".parquet")
                try:
                    total_bytes += parquet_path.stat().st_size
                except OSError:
                    continue
            return {
                "enabled": True,
                "root": str(self._root),
                "entries": len(metas),
                "bytes": total_bytes,
                "capacity": self._capacity,
                "byte_cap": self._byte_cap,
                "hits": self._disk_hits,
                "misses": self._misses,
                "write_errors": self._write_errors,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(list(self._root.glob("*.json"))) if self._root.exists() else 0


# ---------------------------------------------------------------------------
# Cache


class SignalCache:
    """Two-tier (memory LRU + optional disk store) cache of signal plans."""

    def __init__(
        self, capacity: int = DEFAULT_CAPACITY, *, store: SignalPlanStore | None = None
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ValueError("signal cache capacity must be an integer")
        if capacity < 1:
            raise ValueError("signal cache capacity must be >= 1")
        self._capacity = capacity
        self._store = store
        self._entries: OrderedDict[SignalKey, SignalPlan] = OrderedDict()
        self._lock = threading.RLock()
        self._memory_hits = 0
        self._disk_hits = 0
        self._misses = 0

    @property
    def store(self) -> SignalPlanStore | None:
        return self._store

    # -- reading ---------------------------------------------------------

    def get(
        self, key: SignalKey, *, lifecycle_version: str | None = None
    ) -> SignalPlan | None:
        """Return the cached plan or ``None``; memory first, then disk."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and _lifecycle_matches(entry, lifecycle_version):
                self._entries.move_to_end(key)
                self._memory_hits += 1
                return entry
        if self._store is not None:
            plan = self._store.load(key, lifecycle_version=lifecycle_version)
            if plan is not None:
                with self._lock:
                    self._disk_hits += 1
                    self._remember(plan)
                return plan
        with self._lock:
            self._misses += 1
        return None

    def get_or_compute(
        self,
        key: SignalKey,
        factory: Callable[[], SignalPlan],
        *,
        lifecycle_version: str | None = None,
    ) -> CacheHit:
        """Return ``CacheHit(plan, hit, source)``, computing on a miss.

        ``factory`` runs without the lock held so a slow plan never blocks
        readers; two concurrent misses may both compute and the later insert
        wins (the plans are identical by construction).
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and _lifecycle_matches(entry, lifecycle_version):
                self._entries.move_to_end(key)
                self._memory_hits += 1
                return CacheHit(entry, True, "memory")
        if self._store is not None:
            plan = self._store.load(key, lifecycle_version=lifecycle_version)
            if plan is not None:
                with self._lock:
                    self._disk_hits += 1
                    self._remember(plan)
                return CacheHit(plan, True, "disk")
        plan = factory()
        if not isinstance(plan, SignalPlan):
            raise TypeError("signal plan factory must return a SignalPlan")
        if plan.key != key:
            raise ValueError(
                "computed signal plan key does not match the requested key"
            )
        value = self.put(key, plan)
        with self._lock:
            self._misses += 1
            # Another thread may have inserted an identical plan meanwhile.
            entry = self._entries.get(key)
            return CacheHit(entry if entry is not None else value, False, "none")

    # -- writing ---------------------------------------------------------

    def put(self, key: SignalKey, plan: SignalPlan) -> SignalPlan:
        if not isinstance(plan, SignalPlan):
            raise TypeError("signal cache stores SignalPlan values")
        if plan.key != key:
            raise ValueError("signal plan key does not match the cache key")
        with self._lock:
            self._remember(plan)
        if self._store is not None:
            self._store.save(key, plan)
        return plan

    def _remember(self, plan: SignalPlan) -> None:
        # Caller holds the lock.
        self._entries[plan.key] = plan
        self._entries.move_to_end(plan.key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def clear(self, *, disk: bool = False) -> None:
        """Drop memory entries and reset counters; ``disk`` also clears files."""
        with self._lock:
            self._entries.clear()
            self._memory_hits = 0
            self._disk_hits = 0
            self._misses = 0
        if disk and self._store is not None:
            self._store.clear()

    # -- introspection ---------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            memory = {
                "hits": self._memory_hits,
                "entries": len(self._entries),
                "capacity": self._capacity,
            }
            disk_hits = self._disk_hits
            misses = self._misses
        if self._store is not None:
            disk = self._store.stats()
            disk["hits"] = disk_hits
        else:
            disk = {
                "enabled": False,
                "root": None,
                "entries": 0,
                "bytes": 0,
                "capacity": 0,
                "byte_cap": None,
                "hits": 0,
                "misses": 0,
                "write_errors": 0,
            }
        return {
            "hits": memory["hits"] + disk_hits,
            "misses": misses,
            "size": memory["entries"],
            "capacity": self._capacity,
            "memory": memory,
            "disk": disk,
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


DEFAULT_SIGNAL_STORE = SignalPlanStore()
DEFAULT_SIGNAL_CACHE = SignalCache(store=DEFAULT_SIGNAL_STORE)


def cache_stats() -> dict[str, Any]:
    return DEFAULT_SIGNAL_CACHE.stats()


def clear_cache(*, disk: bool = False) -> None:
    DEFAULT_SIGNAL_CACHE.clear(disk=disk)


__all__ = [
    "CacheHit",
    "DEFAULT_CAPACITY",
    "DEFAULT_DISK_BYTE_CAP",
    "DEFAULT_DISK_CAPACITY",
    "DEFAULT_SIGNAL_CACHE",
    "DEFAULT_SIGNAL_STORE",
    "DISK_FORMAT",
    "SignalCache",
    "SignalKey",
    "SignalPlan",
    "SignalPlanStore",
    "SignalRow",
    "cache_stats",
    "clear_cache",
    "compute_signal_plan",
    "declared_warmup",
    "make_signal_key",
    "signal_plan_for_config",
    "signal_spec_hash",
]
