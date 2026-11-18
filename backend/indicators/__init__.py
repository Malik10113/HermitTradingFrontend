"""Indicator registry, Nautilus-backed computation and series cache.

Public surface used by the API and the engine::

    from backend.indicators import build_indicators, compute_series, spec_hash
    from backend.indicators import get_or_compute, make_key, thaw_series

The registry (:mod:`backend.indicators.registry`) is the single source of
parameter and output metadata; :mod:`backend.indicators.compute` is the single
implementation of the indicator math (a thin adapter over NautilusTrader);
:mod:`backend.indicators.cache` memoises the resulting series keyed by data
version, bar type, specification hash and adapter version.
"""

from __future__ import annotations

from backend.indicators.cache import (
    DEFAULT_CACHE,
    CacheKey,
    IndicatorCache,
    cache_stats,
    clear_cache,
    freeze_series,
    get_or_compute,
    make_key,
    thaw_series,
)
from backend.indicators.compute import (
    INDICATOR_IMPL_VERSION,
    IndicatorSet,
    build_indicators,
    canonical_instances,
    compute_series,
    series_to_json,
    spec_hash,
)
from backend.indicators.registry import (
    IndicatorSpec,
    get_spec,
    list_specs,
    registry_json,
    spec_to_json,
    validate_params,
)

__all__ = [
    "CacheKey",
    "DEFAULT_CACHE",
    "INDICATOR_IMPL_VERSION",
    "IndicatorCache",
    "IndicatorSet",
    "IndicatorSpec",
    "build_indicators",
    "cache_stats",
    "canonical_instances",
    "clear_cache",
    "compute_series",
    "freeze_series",
    "get_or_compute",
    "get_spec",
    "list_specs",
    "make_key",
    "registry_json",
    "series_to_json",
    "spec_hash",
    "spec_to_json",
    "thaw_series",
    "validate_params",
]
