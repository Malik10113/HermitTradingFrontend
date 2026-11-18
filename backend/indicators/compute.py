"""Nautilus-backed indicator computation.

One implementation serves the chart and the backtest (see AGENTS.md §6): the
:class:`IndicatorSet` here is the only place indicator math runs, and it feeds
the pinned NautilusTrader indicator classes directly.  There is no Python
re-implementation of any formula; this module is a thin adapter that

* validates the declarative instance list through
  :mod:`backend.indicators.registry`,
* feeds raw bar fields into the matching Nautilus indicator, and
* reports ``None`` — never ``0.0`` — for every output while the indicator is
  still warming up.

Bars are plain mappings with float ``open``/``high``/``low``/``close``/
``volume`` (the canonical schema in AGENTS.md §5).  Only the fields an
indicator declares are read, so a close-only indicator never fails on a bar
that has no volume.
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from nautilus_trader.indicators import (
    AverageTrueRange,
    BollingerBands,
    DonchianChannel,
    ExponentialMovingAverage,
    MovingAverageConvergenceDivergence,
    RelativeStrengthIndex,
    SimpleMovingAverage,
    Stochastics,
    VolumeWeightedAveragePrice,
    WeightedMovingAverage,
)

from backend.indicators import registry

#: Bumped whenever the adapter below changes the values a specification
#: produces (not when a Nautilus version changes — that is already pinned).
#: Part of the cache key in :mod:`backend.indicators.cache`.
INDICATOR_IMPL_VERSION = "1"

NautilusIndicator = Any

Bar = Mapping[str, Any]
Params = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Instance list handling


def _coerce_instances(instances: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate instances in input order, filling parameter defaults."""
    if isinstance(instances, (str, bytes, Mapping)):
        raise ValueError("instances must be a list of indicator instances")
    try:
        raw_instances = list(instances)
    except TypeError as exc:
        raise ValueError("instances must be a list of indicator instances") from exc

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_instances):
        if not isinstance(raw, Mapping):
            raise ValueError(f"instances[{index}] must be an object")
        instance_id = raw.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError(
                f"instances[{index}].instance_id must be a non-empty string"
            )
        if instance_id in seen:
            raise ValueError(f"duplicate instance_id '{instance_id}'")
        seen.add(instance_id)
        indicator_id = raw.get("type")
        if not isinstance(indicator_id, str):
            raise ValueError(f"instances[{index}].type must be a string")
        # Raises ValueError for an unknown indicator, unknown parameter,
        # wrong type or out-of-range value.
        params = registry.validate_params(indicator_id, raw.get("params"))
        out.append(
            {
                "instance_id": instance_id,
                "type": indicator_id,
                "params": params,
            }
        )
    return out


def canonical_instances(
    instances: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Canonical instance list: validated params, sorted by instance id.

    This is the payload hashed into ``spec_hash``; it deliberately drops any
    presentation-only fields (colour, label) and normalises parameters through
    the registry, so an explicit default and an omitted parameter hash
    identically.
    """
    return sorted(
        _coerce_instances(instances), key=lambda item: item["instance_id"]
    )


def spec_hash(instances: Iterable[Mapping[str, Any]]) -> str:
    """SHA-256 over the canonical sorted JSON of the instance list."""
    payload = json.dumps(
        canonical_instances(instances),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Adapter table: one entry per registry indicator


@dataclass(frozen=True)
class _Impl:
    build: Callable[[Params], NautilusIndicator]
    feed: Callable[[NautilusIndicator, Bar, str], None]
    extract: Callable[[NautilusIndicator], dict[str, float]]
    outputs: tuple[str, ...]


def _field(bar: Bar, name: str, instance_id: str) -> float:
    """Read one numeric bar field, failing loudly when it is unusable."""
    try:
        value = bar[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"bar is missing '{name}', required by indicator '{instance_id}'"
        ) from None
    if value is None:
        raise ValueError(
            f"bar['{name}'] is null, required by indicator '{instance_id}'"
        )
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(
            f"bar['{name}'] must be a number for indicator '{instance_id}', "
            f"got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(
            f"bar['{name}'] must be finite for indicator '{instance_id}', got {value!r}"
        )
    return number


def _feed_close(obj: NautilusIndicator, bar: Bar, instance_id: str) -> None:
    obj.update_raw(_field(bar, "close", instance_id))


def _feed_high_low(obj: NautilusIndicator, bar: Bar, instance_id: str) -> None:
    obj.update_raw(
        _field(bar, "high", instance_id), _field(bar, "low", instance_id)
    )


def _feed_high_low_close(obj: NautilusIndicator, bar: Bar, instance_id: str) -> None:
    obj.update_raw(
        _field(bar, "high", instance_id),
        _field(bar, "low", instance_id),
        _field(bar, "close", instance_id),
    )


def _feed_close_volume(obj: NautilusIndicator, bar: Bar, instance_id: str) -> None:
    obj.update_raw(
        _field(bar, "close", instance_id), _field(bar, "volume", instance_id)
    )


def _value(obj: NautilusIndicator) -> dict[str, float]:
    return {"value": obj.value}


def _bands(obj: NautilusIndicator) -> dict[str, float]:
    return {"upper": obj.upper, "middle": obj.middle, "lower": obj.lower}


_IMPLS: dict[str, _Impl] = {
    "sma": _Impl(
        build=lambda p: SimpleMovingAverage(period=p["period"]),
        feed=_feed_close,
        extract=_value,
        outputs=("value",),
    ),
    "ema": _Impl(
        build=lambda p: ExponentialMovingAverage(period=p["period"]),
        feed=_feed_close,
        extract=_value,
        outputs=("value",),
    ),
    "wma": _Impl(
        build=lambda p: WeightedMovingAverage(period=p["period"]),
        feed=_feed_close,
        extract=_value,
        outputs=("value",),
    ),
    "rsi": _Impl(
        build=lambda p: RelativeStrengthIndex(period=p["period"]),
        feed=_feed_close,
        extract=_value,
        outputs=("value",),
    ),
    "macd": _Impl(
        build=lambda p: MovingAverageConvergenceDivergence(
            fast_period=p["fast"],
            slow_period=p["slow"],
            signal_period=p["signal"],
        ),
        feed=_feed_close,
        extract=lambda obj: {
            "value": obj.value,
            "signal": obj.signal,
            "histogram": obj.histogram,
        },
        outputs=("value", "signal", "histogram"),
    ),
    "bollinger": _Impl(
        build=lambda p: BollingerBands(period=p["period"], k=p["k"]),
        feed=_feed_close,
        extract=_bands,
        outputs=("upper", "middle", "lower"),
    ),
    "atr": _Impl(
        build=lambda p: AverageTrueRange(period=p["period"]),
        feed=_feed_high_low_close,
        extract=_value,
        outputs=("value",),
    ),
    "stochastic": _Impl(
        build=lambda p: Stochastics(
            period_k=p["period_k"],
            period_d=p["period_d"],
            slowing=p["slowing"],
        ),
        feed=_feed_high_low_close,
        extract=lambda obj: {"k": obj.value_k, "d": obj.value_d},
        outputs=("k", "d"),
    ),
    "donchian": _Impl(
        build=lambda p: DonchianChannel(period=p["period"]),
        feed=_feed_high_low,
        extract=_bands,
        outputs=("upper", "middle", "lower"),
    ),
    "vwap": _Impl(
        build=lambda _p: VolumeWeightedAveragePrice(),
        feed=_feed_close_volume,
        extract=_value,
        outputs=("value",),
    ),
}


def _impl_for(spec: registry.IndicatorSpec) -> _Impl:
    try:
        impl = _IMPLS[spec.id]
    except KeyError:
        raise ValueError(
            f"indicator '{spec.id}' is registered but has no Nautilus "
            "implementation in backend.indicators.compute"
        ) from None
    expected = tuple(output.key for output in spec.outputs)
    if impl.outputs != expected:
        raise ValueError(
            f"indicator '{spec.id}' adapter outputs {impl.outputs} but the "
            f"registry declares {expected}"
        )
    return impl


# ---------------------------------------------------------------------------
# IndicatorSet


@dataclass
class _Bound:
    instance_id: str
    indicator_id: str
    params: Params
    warmup: int
    outputs: tuple[str, ...]
    impl: _Impl
    obj: NautilusIndicator


class IndicatorSet:
    """A live set of Nautilus indicators fed one bar at a time.

    ``values()`` reports ``None`` for every output of an indicator whose
    ``initialized`` flag is still false, so callers can never mistake warm-up
    for a real zero.  Instances are keyed by ``instance_id``; the underlying
    Nautilus objects stay available through :attr:`indicators` so a strategy
    can read the same objects the chart was rendered from.
    """

    def __init__(self, instances: Iterable[Mapping[str, Any]]) -> None:
        self._bound: list[_Bound] = []
        for item in _coerce_instances(instances):
            spec = registry.get_spec(item["type"])
            impl = _impl_for(spec)
            self._bound.append(
                _Bound(
                    instance_id=item["instance_id"],
                    indicator_id=item["type"],
                    params=item["params"],
                    warmup=max(int(spec.warmup(item["params"])), 0),
                    outputs=impl.outputs,
                    impl=impl,
                    obj=impl.build(item["params"]),
                )
            )
        self._by_id = {bound.instance_id: bound for bound in self._bound}
        self._indicators = MappingProxyType(
            {bound.instance_id: bound.obj for bound in self._bound}
        )

    # -- feeding ---------------------------------------------------------

    def update(self, bar: Bar) -> None:
        """Feed one canonical bar to every indicator in the set."""
        if not isinstance(bar, Mapping):
            raise ValueError("bar must be a mapping with OHLCV fields")
        for bound in self._bound:
            bound.impl.feed(bound.obj, bar, bound.instance_id)

    # -- reading ---------------------------------------------------------

    def values(self) -> dict[str, dict[str, float | None]]:
        """``instance_id -> output key -> value``; ``None`` during warm-up."""
        return {bound.instance_id: self._values_of(bound) for bound in self._bound}

    def initialized(self) -> dict[str, bool]:
        return {
            bound.instance_id: bool(bound.obj.initialized) for bound in self._bound
        }

    def warmup(self) -> dict[str, int]:
        """Declared warm-up (leading null) count per instance."""
        return {bound.instance_id: bound.warmup for bound in self._bound}

    @property
    def outputs(self) -> dict[str, list[str]]:
        return {bound.instance_id: list(bound.outputs) for bound in self._bound}

    @property
    def indicators(self) -> Mapping[str, NautilusIndicator]:
        """Stable mapping of instance id to the live Nautilus indicator."""
        return self._indicators

    def get(self, instance_id: str) -> NautilusIndicator:
        return self._by_id[instance_id].obj

    def __len__(self) -> int:
        return len(self._bound)

    def __contains__(self, instance_id: object) -> bool:
        return instance_id in self._by_id

    # -- internals -------------------------------------------------------

    def _values_of(self, bound: _Bound) -> dict[str, float | None]:
        if not bound.obj.initialized:
            return {key: None for key in bound.outputs}
        extracted = bound.impl.extract(bound.obj)
        return {key: float(extracted[key]) for key in bound.outputs}


def build_indicators(instances: Iterable[Mapping[str, Any]]) -> IndicatorSet:
    """Build an :class:`IndicatorSet` from ``{instance_id,type,params}`` dicts."""
    return IndicatorSet(instances)


# ---------------------------------------------------------------------------
# Batch helpers


def compute_series(
    bars: Sequence[Bar],
    instances: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, list[float | None]]]:
    """Compute full series for every instance over ``bars``.

    Returns ``instance_id -> output key -> list`` where each list has exactly
    ``len(bars)`` entries; warm-up (and any bar that leaves an output
    uninitialised) is ``None``.
    """
    indicator_set = build_indicators(instances)
    series: dict[str, dict[str, list[float | None]]] = {
        bound.instance_id: {key: [] for key in bound.outputs}
        for bound in indicator_set._bound
    }
    for bar in bars:
        indicator_set.update(bar)
        for bound in indicator_set._bound:
            if bound.obj.initialized:
                extracted = bound.impl.extract(bound.obj)
                for key in bound.outputs:
                    series[bound.instance_id][key].append(float(extracted[key]))
            else:
                for key in bound.outputs:
                    series[bound.instance_id][key].append(None)
    return series


def series_to_json(
    series: Mapping[str, Mapping[str, Sequence[float | None]]],
) -> dict[str, dict[str, list[float | None]]]:
    """Return a JSON-safe copy of ``series``.

    ``None`` is preserved as JSON ``null``; non-finite floats (which JSON
    cannot represent) become ``null`` rather than an invented number.
    """
    out: dict[str, dict[str, list[float | None]]] = {}
    for instance_id, by_output in series.items():
        out[instance_id] = {}
        for key, values in by_output.items():
            out[instance_id][key] = [
                None
                if value is None
                else (float(value) if math.isfinite(float(value)) else None)
                for value in values
            ]
    return out


__all__ = [
    "INDICATOR_IMPL_VERSION",
    "IndicatorSet",
    "build_indicators",
    "canonical_instances",
    "compute_series",
    "series_to_json",
    "spec_hash",
]
