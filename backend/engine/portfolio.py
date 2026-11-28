"""Deterministic portfolio optimisation over stored backtest runs.

This module ports the sibling research pipeline's three portfolio building
blocks into the dashboard's engine package:

* ``kelly.py`` -- a fractional-Kelly stake rule with a lower-confidence-bound
  (LCB) edge estimate and a hard bankroll cap;
* ``weighting.py`` -- five frozen weighting rules (equal, inverse volatility,
  risk parity, exponential, shrunk mean-variance) sharing one water-filling
  weight cap;
* ``allocation.py`` -- the three-stage correlation-penalised allocator.

Everything here is a pure function of the arguments: no module state, no RNG,
no clock, no I/O.  Equity points are read from stored run artifacts and are
timestamped in UTC nanoseconds; every derived number comes from those points.
The output is a historical simulation summary, never a claim that a strategy
works or should be traded.

Provenance: ``FundedBetBacktesting/src/polymarket_research/portfolio/``
(``kelly.py``, ``weighting.py``, ``allocation.py``).

Alignment
---------
Runs are aligned on the union of UTC calendar days when they span at least
three days.  Each run's equity is forward-filled from its first observation and
day-to-day returns are ``eq[t] / eq[t-1] - 1.0``.  Before a run's first
observation there is no equity to compare, so those aligned steps carry no
return (``NaN`` in the internal series, ``0.0`` contribution to the composed
portfolio curve) rather than a fabricated flat zero -- a sleeve that starts late
is measured over its active window only.  When the runs span fewer than
three days -- typical for intraday single-match backtests -- the union of bar
timestamps is used instead and the output carries an ``intraday_bar_returns``
warning.  The first aligned point has no predecessor and therefore no return;
returns are never padded with a fabricated zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from backend.engine.analytics import _daily_equity

PORTFOLIO_METHODS: tuple[str, ...] = (
    "equal",
    "inverse_volatility",
    "risk_parity",
    "exponential",
    "shrunk_mean_variance",
    "three_stage",
)
"""Weighting methods understood by :func:`compute_weights`."""

PROVENANCE = (
    "FundedBetBacktesting/src/polymarket_research/portfolio/"
    "{kelly,weighting,allocation}.py"
)

_VOL_FLOOR = 1e-6
"""Default daily-volatility floor for inverse-volatility weights."""

_PSD_FLOOR = 1e-16
"""Variance floor on a covariance diagonal (before PSD projection)."""

_DEFAULT_MAX_WEIGHT = 0.35
"""Per-sleeve cap matching the sibling ``AllocationConfig`` default."""


# ---------------------------------------------------------------------------
# Kelly stake sizing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KellyConfig:
    """Frozen parameters of the daily fractional-Kelly stake rule.

    ``min_periods`` defaults to ``5`` rather than the sibling's ``20`` so a
    short multi-day or single-match series can still be sized; it is the
    caller's choice to raise it up to ``window``.
    """

    fraction: float = 0.25
    max_stake: float = 0.05
    min_stake: float = 0.0
    lcb_z: float = 1.0
    window: int = 60
    min_periods: int = 5
    variance_floor: float = 1e-8

    def __post_init__(self) -> None:
        if not (0.0 < self.fraction <= 1.0):
            raise ValueError("fraction must be in (0, 1]")
        if not (0.0 < self.max_stake <= 1.0):
            raise ValueError("max_stake must be in (0, 1]")
        if not (0.0 <= self.min_stake <= self.max_stake):
            raise ValueError("min_stake must be in [0, max_stake]")
        if not (self.lcb_z >= 0.0):
            raise ValueError("lcb_z must be >= 0")
        if self.window < 2:
            raise ValueError("window must be >= 2")
        if self.min_periods < 2:
            raise ValueError("min_periods must be >= 2")
        if self.min_periods > self.window:
            raise ValueError("min_periods must be <= window")
        if not (self.variance_floor > 0.0):
            raise ValueError("variance_floor must be > 0")

    def payload(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the frozen posture."""
        return {
            "fraction": self.fraction,
            "max_stake": self.max_stake,
            "min_stake": self.min_stake,
            "lcb_z": self.lcb_z,
            "window": self.window,
            "min_periods": self.min_periods,
            "variance_floor": self.variance_floor,
        }


def kelly_stake_fraction(
    returns: Sequence[float],
    *,
    config: KellyConfig | None = None,
) -> float:
    """Fraction of the bankroll to risk on the next period.

    Filters non-finite observations, keeps the trailing ``window`` values and
    applies ``fraction * lcb_mean / max(variance, variance_floor)`` clipped to
    ``[min_stake, max_stake]``.  Returns exactly ``0.0`` -- never
    ``min_stake`` -- when there are fewer than ``min_periods`` observations or
    the LCB mean is non-positive: no evidence means no stake.
    """
    cfg = config or KellyConfig()
    raw_values: list[float] = []
    for value in returns:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            raw_values.append(number)
    values = raw_values[-cfg.window :]
    n_observations = len(values)
    if n_observations < cfg.min_periods or n_observations < 2:
        return 0.0

    mean = sum(values) / n_observations
    variance = sum((value - mean) ** 2 for value in values) / (n_observations - 1)
    if not (math.isfinite(mean) and math.isfinite(variance)):
        return 0.0

    standard_error = math.sqrt(variance / n_observations)
    lcb_mean = mean - cfg.lcb_z * standard_error
    if lcb_mean <= 0.0:
        return 0.0

    raw = cfg.fraction * lcb_mean / max(variance, cfg.variance_floor)
    return float(min(max(raw, cfg.min_stake), cfg.max_stake))


# ---------------------------------------------------------------------------
# Weight caps and shared statistics
# ---------------------------------------------------------------------------


def cap_weights(
    raw_weights: Mapping[str, float],
    max_weight: float,
) -> dict[str, float]:
    """Normalise, scale to the deployable gross and water-fill ``max_weight``.

    Positive raw weights are normalised to one, scaled by
    ``min(1, max_weight * n_positive)`` and then capped: any sleeve above
    ``max_weight`` is fixed there and the excess is redistributed
    proportionally over the still-uncapped sleeves.  With
    ``max_weight * n_positive < 1`` the shortfall stays in cash rather than
    being levered back into the book.  Non-positive raw weights stay zero.
    """
    if not (0.0 < max_weight <= 1.0):
        raise ValueError("max_weight must be in (0, 1]")

    values: dict[str, float] = {}
    for key, value in raw_weights.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"weight for {key!r} must be numeric") from exc
        if not math.isfinite(number):
            raise ValueError(f"weight for {key!r} must be finite")
        values[str(key)] = number

    total = sum(max(value, 0.0) for value in values.values())
    if total <= 0.0:
        return {key: 0.0 for key in values}

    positive = [key for key, value in values.items() if value > 0.0]
    scale = min(1.0, max_weight * len(positive)) / total
    weights = {key: max(value, 0.0) * scale for key, value in values.items()}

    capped: set[str] = set()
    for _ in range(len(positive) + 1):
        newly = [
            key
            for key in weights
            if key not in capped and weights[key] > max_weight + 1e-15
        ]
        if not newly:
            break
        excess = sum(weights[key] - max_weight for key in newly)
        for key in newly:
            weights[key] = max_weight
            capped.add(key)
        free = [
            key for key in weights if key not in capped and weights[key] > 0.0
        ]
        free_total = sum(weights[key] for key in free)
        if free_total <= 0.0:
            break
        for key in free:
            weights[key] += excess * weights[key] / free_total

    return {key: min(max(value, 0.0), max_weight) for key, value in weights.items()}


def _numeric(value: object) -> float:
    """Coerce to a finite float, or ``NaN`` when it cannot be one."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _finite_tail(values: Sequence[float], window: int) -> list[float]:
    """Return the last ``window`` finite observations, oldest first."""
    finite = [number for number in map(_numeric, values) if math.isfinite(number)]
    if window <= 0:
        return []
    return finite[-window:]


def _sample_std(values: Sequence[float]) -> float | None:
    """Sample standard deviation (``ddof=1``); ``None`` with fewer than two."""
    finite = [number for number in map(_numeric, values) if math.isfinite(number)]
    if len(finite) < 2:
        return None
    mean = sum(finite) / len(finite)
    variance = sum((value - mean) ** 2 for value in finite) / (len(finite) - 1)
    if variance < 0.0:  # pragma: no cover - floating-point guard only
        return None
    return math.sqrt(variance)


def _sample_mean(values: Sequence[float]) -> float | None:
    finite = [number for number in map(_numeric, values) if math.isfinite(number)]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _pairwise_covariance(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, int]:
    """Sample covariance over the positions where both series are finite."""
    pairs = [
        (a, b)
        for a, b in zip(map(_numeric, left), map(_numeric, right))
        if math.isfinite(a) and math.isfinite(b)
    ]
    if len(pairs) < 2:
        return 0.0, len(pairs)
    mean_a = sum(a for a, _ in pairs) / len(pairs)
    mean_b = sum(b for _, b in pairs) / len(pairs)
    covariance = sum((a - mean_a) * (b - mean_b) for a, b in pairs) / (
        len(pairs) - 1
    )
    return covariance, len(pairs)


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    """Pearson correlation in ``[-1, 1]``; ``0.0`` when undefined.

    Means, variances and the covariance are all taken over the *same* paired
    sample (positions where both series are finite), so the result is a true
    Pearson correlation bounded by ``[-1, 1]`` even when the two series cover
    different spans.  An undefined correlation (too few paired observations or
    zero variance) is reported as ``0.0`` -- no measured co-movement -- never
    as a fabricated strong relationship.
    """
    pairs = [
        (a, b)
        for a, b in zip(map(_numeric, left), map(_numeric, right))
        if math.isfinite(a) and math.isfinite(b)
    ]
    if len(pairs) < 2:
        return 0.0
    count = len(pairs)
    mean_left = sum(a for a, _ in pairs) / count
    mean_right = sum(b for _, b in pairs) / count
    var_left = sum((a - mean_left) ** 2 for a, _ in pairs) / (count - 1)
    var_right = sum((b - mean_right) ** 2 for _, b in pairs) / (count - 1)
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in pairs
    ) / (count - 1)
    denominator = math.sqrt(var_left * var_right)
    if not math.isfinite(denominator) or denominator <= 0.0:
        return 0.0
    value = covariance / denominator
    return float(min(max(value, -1.0), 1.0))


def _covariance_matrix(
    series: Mapping[str, Sequence[float]], ids: Sequence[str]
) -> np.ndarray:
    """Pairwise sample covariance, symmetrised with a floored diagonal."""
    size = len(ids)
    sigma = np.zeros((size, size), dtype=np.float64)
    for i, left_id in enumerate(ids):
        for j in range(i, size):
            right_id = ids[j]
            covariance, _ = _pairwise_covariance(series[left_id], series[right_id])
            sigma[i, j] = covariance
            sigma[j, i] = covariance
    np.fill_diagonal(sigma, np.maximum(np.diag(sigma), _PSD_FLOOR))
    return sigma


def _psd_project(sigma: np.ndarray, floor: float = _PSD_FLOOR) -> np.ndarray:
    """Project a symmetric covariance matrix onto the PSD cone."""
    symmetric = 0.5 * (sigma + sigma.T)
    np.fill_diagonal(symmetric, np.maximum(np.diag(symmetric), floor))
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    eigenvalues = np.maximum(eigenvalues, floor)
    projected = (eigenvectors * eigenvalues) @ eigenvectors.T
    projected = 0.5 * (projected + projected.T)
    np.fill_diagonal(projected, np.maximum(np.diag(projected), floor))
    return projected


# ---------------------------------------------------------------------------
# Weighting rules
# ---------------------------------------------------------------------------


def _eligible_ids(
    returns_by_id: Mapping[str, Sequence[float]], min_observations: int = 2
) -> list[str]:
    """Sleeves with at least ``min_observations`` finite returns, input order."""
    return [
        str(identifier)
        for identifier, values in returns_by_id.items()
        if len(_finite_tail(values, len(values))) >= min_observations
    ]


def _finalise(
    raw: Mapping[str, float],
    all_ids: Sequence[str],
    *,
    max_weight: float,
    all_zero_ok: bool,
    fallback_ids: Sequence[str] | None = None,
) -> dict[str, float]:
    """Clip raw scores, apply the fallback rule and cap the result.

    ``fallback_ids`` names the sleeves the equal-weight fallback may touch;
    it defaults to every input id.  Sleeves outside it are held at ``0.0`` no
    matter what the raw rule returned, so a sleeve without enough history can
    never pick up weight through the fallback.
    """
    support = set(all_ids if fallback_ids is None else fallback_ids)
    clipped: dict[str, float] = {}
    for identifier in all_ids:
        try:
            value = float(raw.get(identifier, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            raise ValueError("weight rule produced a non-finite raw weight")
        clipped[identifier] = max(value, 0.0) if identifier in support else 0.0

    if sum(clipped.values()) <= 0.0:
        if all_zero_ok:
            return {identifier: 0.0 for identifier in all_ids}
        clipped = {
            identifier: 1.0 if identifier in support else 0.0
            for identifier in all_ids
        }
    return cap_weights(clipped, max_weight)


def _inverse_volatility_raw(
    returns_by_id: Mapping[str, Sequence[float]],
    identifiers: Sequence[str],
    *,
    vol_window: int,
    min_periods: int,
    vol_floor: float,
) -> dict[str, float]:
    raw = {identifier: 0.0 for identifier in identifiers}
    for identifier in identifiers:
        window = _finite_tail(returns_by_id[identifier], vol_window)
        if len(window) < min_periods:
            continue
        std = _sample_std(window)
        if std is None:
            continue
        raw[identifier] = 1.0 / max(std, vol_floor)
    return raw


def _inverse_volatility_weights(
    returns_by_id: Mapping[str, Sequence[float]],
    identifiers: Sequence[str],
    *,
    max_weight: float,
    vol_window: int,
    min_periods: int,
    vol_floor: float,
) -> dict[str, float]:
    raw = _inverse_volatility_raw(
        returns_by_id,
        identifiers,
        vol_window=vol_window,
        min_periods=min_periods,
        vol_floor=vol_floor,
    )
    return _finalise(
        raw,
        identifiers,
        max_weight=max_weight,
        all_zero_ok=False,
        fallback_ids=identifiers,
    )


def _risk_parity_raw(
    returns_by_id: Mapping[str, Sequence[float]],
    identifiers: Sequence[str],
    *,
    vol_window: int,
    min_periods: int,
    iterations: int = 100,
) -> dict[str, float]:
    raw = {identifier: 0.0 for identifier in identifiers}
    usable = [
        identifier
        for identifier in identifiers
        if len(_finite_tail(returns_by_id[identifier], vol_window)) >= min_periods
    ]
    if not usable:
        return raw

    tail = {
        identifier: _finite_tail(returns_by_id[identifier], vol_window)
        for identifier in usable
    }
    sigma = _psd_project(_covariance_matrix(tail, usable))
    vols = np.sqrt(np.diag(sigma))
    weights = 1.0 / np.maximum(vols, math.sqrt(_PSD_FLOOR))
    total = float(weights.sum())
    if total <= 0.0:  # pragma: no cover - positive floor keeps this positive
        return raw
    weights = weights / total

    for _ in range(iterations):
        marginal = np.maximum(sigma @ weights, _PSD_FLOOR)
        contributions = weights * marginal
        target = float(weights @ marginal) / weights.size
        updated = weights * np.sqrt(
            target / np.maximum(contributions, 1e-32)
        )
        updated_total = float(updated.sum())
        if updated_total <= 0.0:  # pragma: no cover - positive start
            break
        updated = updated / updated_total
        if float(np.max(np.abs(updated - weights))) < 1e-12:
            weights = updated
            break
        weights = updated

    for identifier, value in zip(usable, weights):
        raw[identifier] = float(value)
    return raw


def _exponential_raw(
    returns_by_id: Mapping[str, Sequence[float]],
    identifiers: Sequence[str],
    *,
    half_life_days: float = 20.0,
) -> dict[str, float]:
    """Positive part of an exponentially decayed mean (one step = one day)."""
    raw: dict[str, float] = {}
    for identifier in identifiers:
        values = _finite_tail(
            returns_by_id[identifier], len(returns_by_id[identifier])
        )
        if not values:
            raw[identifier] = 0.0
            continue
        ages = [len(values) - 1 - position for position in range(len(values))]
        decayed = [0.5 ** (age / half_life_days) for age in ages]
        denominator = sum(decayed)
        if denominator <= 0.0:  # pragma: no cover - decay is strictly positive
            raw[identifier] = 0.0
            continue
        mean = sum(value * weight for value, weight in zip(values, decayed)) / denominator
        raw[identifier] = max(mean, 0.0)
    return raw


def _shrunk_mean_variance_raw(
    returns_by_id: Mapping[str, Sequence[float]],
    identifiers: Sequence[str],
    *,
    vol_window: int,
    shrinkage: float = 0.5,
) -> dict[str, float]:
    raw = {identifier: 0.0 for identifier in identifiers}
    usable = [
        identifier
        for identifier in identifiers
        if len(_finite_tail(returns_by_id[identifier], vol_window)) >= 2
    ]
    if not usable:
        return raw

    tail = {
        identifier: _finite_tail(returns_by_id[identifier], vol_window)
        for identifier in usable
    }
    means = np.array(
        [_sample_mean(tail[identifier]) or 0.0 for identifier in usable],
        dtype=np.float64,
    )
    sigma = _psd_project(_covariance_matrix(tail, usable))
    average_variance = float(np.mean(np.diag(sigma)))
    target = np.eye(len(usable), dtype=np.float64) * average_variance
    shrunk = (1.0 - shrinkage) * sigma + shrinkage * target
    jitter = 1e-12 * max(average_variance, _PSD_FLOOR)
    shrunk = shrunk + np.eye(len(usable), dtype=np.float64) * jitter

    solution = np.linalg.solve(shrunk, means)
    for identifier, value in zip(usable, np.maximum(solution, 0.0)):
        raw[identifier] = float(value)
    return raw


def _validate_options(
    *,
    max_weight: float,
    vol_window: int,
    min_periods: int,
    correlation_penalty: float,
    max_pair_correlation: float,
) -> None:
    if not (0.0 < max_weight <= 1.0):
        raise ValueError("max_weight must be in (0, 1]")
    if vol_window < 2:
        raise ValueError("vol_window must be >= 2")
    if min_periods < 2:
        raise ValueError("min_periods must be >= 2")
    if min_periods > vol_window:
        raise ValueError("min_periods must be <= vol_window")
    if not (0.0 <= correlation_penalty <= 2.0):
        raise ValueError("correlation_penalty must be in [0, 2]")
    if not (-1.0 < max_pair_correlation <= 1.0):
        raise ValueError("max_pair_correlation must be in (-1, 1]")


def compute_weights(
    returns_by_id: Mapping[str, Sequence[float]],
    *,
    method: str = "three_stage",
    max_weight: float = _DEFAULT_MAX_WEIGHT,
    vol_window: int = 20,
    min_periods: int = 2,
    correlation_penalty: float = 0.5,
    max_pair_correlation: float = 0.95,
) -> dict[str, float]:
    """Return portfolio weights for one frozen weighting method.

    ``returns_by_id`` maps a run id to its aligned return series (oldest first;
    the last value is the most recent observation).  Every input id appears in
    the result; ineligible sleeves and sleeves the rule zeroes get ``0.0``.
    Variance-based rules need at least ``min_periods`` finite observations
    (and never fewer than two); ``equal`` and ``exponential`` need one.  A
    sleeve below its rule's minimum is never funded, not even by the
    equal-weight fallback of a rule that produced no positive score.
    Weights are non-negative and sum to at most one -- the residual is cash when
    ``max_weight`` cannot deploy the whole book.
    """
    if method not in PORTFOLIO_METHODS:
        raise ValueError(
            f"unknown method {method!r}; expected one of {PORTFOLIO_METHODS}"
        )
    _validate_options(
        max_weight=max_weight,
        vol_window=vol_window,
        min_periods=min_periods,
        correlation_penalty=correlation_penalty,
        max_pair_correlation=max_pair_correlation,
    )

    returns_by_id = {
        str(identifier): values for identifier, values in returns_by_id.items()
    }
    all_ids = list(returns_by_id)
    if not all_ids:
        return {}

    if method == "three_stage":
        allocation = three_stage_allocate(
            returns_by_id,
            max_weight=max_weight,
            vol_window=vol_window,
            min_periods=min_periods,
            correlation_penalty=correlation_penalty,
            max_pair_correlation=max_pair_correlation,
        )
        weights = allocation["weights"]
        return {identifier: float(weights.get(identifier, 0.0)) for identifier in all_ids}

    # Every rule except equal/exponential needs a variance estimate, so its
    # minimum evidence is ``min_periods`` (never below two observations).
    # Sleeves below that stay at 0.0 for this run -- including in the
    # equal-weight fallback, which must never fund an unmeasured sleeve.
    eff_min = 1 if method in ("equal", "exponential") else max(int(min_periods), 2)
    eligible_ids = _eligible_ids(returns_by_id, eff_min)
    if not eligible_ids:
        return {identifier: 0.0 for identifier in all_ids}

    if method == "equal":
        raw = {identifier: 1.0 for identifier in eligible_ids}
        return _finalise(
            raw,
            all_ids,
            max_weight=max_weight,
            all_zero_ok=True,
            fallback_ids=eligible_ids,
        )

    if method == "inverse_volatility":
        weights = _inverse_volatility_weights(
            returns_by_id,
            eligible_ids,
            max_weight=max_weight,
            vol_window=vol_window,
            min_periods=min_periods,
            vol_floor=_VOL_FLOOR,
        )
        return {identifier: float(weights.get(identifier, 0.0)) for identifier in all_ids}

    if method == "risk_parity":
        raw = _risk_parity_raw(
            returns_by_id,
            eligible_ids,
            vol_window=vol_window,
            min_periods=min_periods,
        )
        return _finalise(
            raw,
            all_ids,
            max_weight=max_weight,
            all_zero_ok=False,
            fallback_ids=eligible_ids,
        )

    if method == "exponential":
        raw = _exponential_raw(returns_by_id, eligible_ids)
        return _finalise(
            raw,
            all_ids,
            max_weight=max_weight,
            all_zero_ok=True,
            fallback_ids=eligible_ids,
        )

    # shrunk_mean_variance: fall back to inverse volatility when the clipped
    # long-only solution is empty (no positive mean signal).
    raw = _shrunk_mean_variance_raw(
        returns_by_id, eligible_ids, vol_window=vol_window
    )
    if sum(raw.values()) <= 0.0:
        weights = _inverse_volatility_weights(
            returns_by_id,
            eligible_ids,
            max_weight=max_weight,
            vol_window=vol_window,
            min_periods=min_periods,
            vol_floor=_VOL_FLOOR,
        )
        return {identifier: float(weights.get(identifier, 0.0)) for identifier in all_ids}
    return _finalise(
        raw,
        all_ids,
        max_weight=max_weight,
        all_zero_ok=False,
        fallback_ids=eligible_ids,
    )


def three_stage_allocate(
    returns_by_id: Mapping[str, Sequence[float]],
    *,
    max_weight: float = _DEFAULT_MAX_WEIGHT,
    vol_window: int = 20,
    min_periods: int = 2,
    correlation_penalty: float = 0.5,
    max_pair_correlation: float = 0.95,
    variance_floor: float = 1e-8,
) -> dict[str, Any]:
    """Correlation-penalised allocation in the sibling's three stages.

    Stage 1 is inverse volatility over sleeves with at least ``min_periods``
    return observations (never fewer than two).  Stage 2 collapses
    near-duplicates: sleeves are visited by ascending volatility (then run id)
    and a sleeve correlated at or above ``max_pair_correlation`` with an
    already-kept sleeve is dropped, keeping the calmer one.  Stage 3 reallocates
    the survivors by inverse volatility penalised by
    ``1 / (1 + correlation_penalty * mean_positive_correlation)``
    and applies the shared water-filling cap.

    Returns the weights over every input id plus the audit trail: ``dropped``,
    ``reasons``, ``reallocated`` (first-pass mass of collapsed sleeves),
    ``effective_n``, ``max_pair_correlation`` among active sleeves and
    ``kept``.
    """
    if variance_floor <= 0.0:
        raise ValueError("variance_floor must be > 0")
    _validate_options(
        max_weight=max_weight,
        vol_window=vol_window,
        min_periods=min_periods,
        correlation_penalty=correlation_penalty,
        max_pair_correlation=max_pair_correlation,
    )

    returns_by_id = {
        str(identifier): values for identifier, values in returns_by_id.items()
    }
    all_ids = list(returns_by_id)
    if not all_ids:
        return {
            "weights": {},
            "dropped": [],
            "reasons": {},
            "reallocated": 0.0,
            "effective_n": 0.0,
            "max_pair_correlation": 0.0,
            "kept": [],
        }

    vol_floor = math.sqrt(variance_floor)
    reasons: dict[str, str] = {}
    dropped: list[str] = []

    # --- Stage 1: inverse-vol first pass over sleeves with evidence ----------
    # The same evidence floor the other variance-based rules use: a sleeve with
    # fewer than ``min_periods`` finite observations (never fewer than two) is
    # not funded here either.
    eff_min = max(int(min_periods), 2)
    eligible: list[str] = []
    stds: dict[str, float] = {}
    for identifier in all_ids:
        window = _finite_tail(returns_by_id[identifier], vol_window)
        if len(window) < eff_min:
            dropped.append(identifier)
            reasons[identifier] = (
                f"insufficient return observations ({len(window)} < {eff_min})"
            )
            continue
        std = _sample_std(window)
        if std is None:
            dropped.append(identifier)
            reasons[identifier] = (
                f"insufficient return observations ({len(window)} < {eff_min})"
            )
            continue
        eligible.append(identifier)
        stds[identifier] = max(std, vol_floor)

    if not eligible:
        return {
            "weights": {identifier: 0.0 for identifier in all_ids},
            "dropped": list(dict.fromkeys(dropped)),
            "reasons": reasons,
            "reallocated": 0.0,
            "effective_n": 0.0,
            "max_pair_correlation": 0.0,
            "kept": [],
        }

    inverse = {identifier: 1.0 / stds[identifier] for identifier in eligible}
    inverse_total = sum(inverse.values())
    first_pass = {
        identifier: inverse[identifier] / inverse_total for identifier in eligible
    }

    # --- Stage 2: collapse near-duplicates, keeping the calmer sleeve --------
    correlations = {
        (left, right): _correlation(
            returns_by_id[left], returns_by_id[right]
        )
        for left in eligible
        for right in eligible
    }
    ordered = sorted(eligible, key=lambda identifier: (stds[identifier], identifier))
    kept: list[str] = []
    for identifier in ordered:
        duplicate = None
        for keeper in kept:
            value = correlations[(identifier, keeper)]
            if value >= max_pair_correlation:
                duplicate = keeper
                break
        if duplicate is not None:
            dropped.append(identifier)
            reasons[identifier] = (
                f"correlation >= {max_pair_correlation:.2f} with {duplicate}"
            )
            continue
        kept.append(identifier)

    # --- Stage 3: reallocate with a correlation penalty ----------------------
    penalties: dict[str, float] = {}
    for identifier in kept:
        others = [other for other in kept if other != identifier]
        if not others:
            penalties[identifier] = 1.0
            continue
        mean_corr = sum(
            max(correlations[(identifier, other)], 0.0) for other in others
        ) / len(others)
        penalties[identifier] = 1.0 / (
            1.0 + correlation_penalty * max(mean_corr, 0.0)
        )

    raw = {
        identifier: inverse[identifier] * penalties[identifier]
        for identifier in kept
    }
    raw_total = sum(raw.values())
    if raw_total <= 0.0:  # pragma: no cover - floors keep this positive
        raw = {identifier: 1.0 for identifier in kept}
        raw_total = float(len(kept))
    normalised = {identifier: raw[identifier] / raw_total for identifier in kept}

    weights = cap_weights(normalised, max_weight)
    full_weights = {
        identifier: float(weights.get(identifier, 0.0)) for identifier in all_ids
    }

    squared = sum(value * value for value in full_weights.values())
    effective_n = (1.0 / squared) if squared > 0.0 else 0.0
    active = [identifier for identifier in all_ids if full_weights[identifier] > 0.0]
    pairwise = 0.0
    if len(active) >= 2:
        pairwise = max(
            correlations[(left, right)]
            for position, left in enumerate(active)
            for right in active[position + 1 :]
        )

    return {
        "weights": full_weights,
        "dropped": list(dict.fromkeys(dropped)),
        "reasons": reasons,
        "reallocated": float(
            sum(first_pass[identifier] for identifier in eligible if identifier not in kept)
        ),
        "effective_n": float(effective_n),
        "max_pair_correlation": float(pairwise),
        "kept": list(kept),
    }


# ---------------------------------------------------------------------------
# High-level run composition
# ---------------------------------------------------------------------------


def _utc_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date()


def _iso(ts_ns: int) -> str:
    return (
        datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _day_start_ns(day: date) -> int:
    return int(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )


def _clean_points(equity: Sequence[Mapping[str, Any]] | None) -> list[tuple[int, float]]:
    """Sorted, timestamp-deduplicated ``(ts_ns, equity_usd)`` points."""
    points: list[tuple[int, float]] = []
    for point in equity or []:
        if not isinstance(point, Mapping):
            continue
        ts_ns = point.get("ts_ns")
        equity_usd = point.get("equity_usd")
        if ts_ns is None or equity_usd is None:
            continue
        try:
            ts = int(ts_ns)
            value = float(equity_usd)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        points.append((ts, value))
    points.sort(key=lambda item: item[0])

    deduped: list[tuple[int, float]] = []
    for ts, value in points:
        if deduped and deduped[-1][0] == ts:
            deduped[-1] = (ts, value)
        else:
            deduped.append((ts, value))
    return deduped


def _series_returns(values: Sequence[float]) -> list[float]:
    """Bar-to-bar returns; a non-positive predecessor yields ``0.0``."""
    returns: list[float] = []
    for previous, current in zip(values, values[1:]):
        if not (math.isfinite(previous) and math.isfinite(current)):
            returns.append(math.nan)
        elif previous > 0.0:
            returns.append(current / previous - 1.0)
        else:
            returns.append(0.0)
    return returns


def _annualised_sharpe(returns: Sequence[float]) -> float | None:
    finite = [value for value in returns if math.isfinite(value)]
    if len(finite) < 2:
        return None
    mean = sum(finite) / len(finite)
    variance = sum((value - mean) ** 2 for value in finite) / (len(finite) - 1)
    if variance <= 0.0:
        return None
    return mean / math.sqrt(variance) * math.sqrt(365.0)


def _annualised_vol(returns: Sequence[float]) -> float | None:
    std = _sample_std(returns)
    if std is None:
        return None
    return std * math.sqrt(365.0)


def optimize_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    method: str = "three_stage",
    max_weight: float = _DEFAULT_MAX_WEIGHT,
    vol_window: int = 20,
    min_periods: int = 2,
    correlation_penalty: float = 0.5,
    max_pair_correlation: float = 0.95,
    bankroll_usd: float = 1000.0,
    kelly_config: KellyConfig | None = None,
    kelly_fraction: float | None = None,
    lcb_z: float | None = None,
) -> dict[str, Any]:
    """Compose stored backtest runs into one weighted portfolio.

    Each run is ``{run_id, instrument_id, strategy_hash, requested_profile,
    effective_profile, equity, metrics}``; ``equity`` carries stored
    ``ts_ns``/``equity_usd`` points.  See the module docstring for the
    daily-versus-intraday alignment rule.

    The returned mapping matches the dashboard's portfolio contract: frozen
    weights and Kelly fractions per run, the pairwise correlation matrix, the
    composed equity curve normalised to ``100.0`` at the first aligned
    timestamp, summary metrics, the member audit trail, provenance and advisory
    warnings.  It is a historical simulation summary -- not investment advice.
    """
    if method not in PORTFOLIO_METHODS:
        raise ValueError(
            f"unknown method {method!r}; expected one of {PORTFOLIO_METHODS}"
        )
    _validate_options(
        max_weight=max_weight,
        vol_window=vol_window,
        min_periods=min_periods,
        correlation_penalty=correlation_penalty,
        max_pair_correlation=max_pair_correlation,
    )
    if not (math.isfinite(bankroll_usd) and bankroll_usd > 0.0):
        raise ValueError("bankroll_usd must be finite and > 0")

    config = kelly_config or KellyConfig()
    if kelly_fraction is not None:
        config = replace(config, fraction=float(kelly_fraction))
    if lcb_z is not None:
        config = replace(config, lcb_z=float(lcb_z))

    if not runs:
        raise ValueError("optimize_runs requires at least one run")

    run_ids: list[str] = []
    points_by_id: dict[str, list[tuple[int, float]]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for run in runs:
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("every run must carry a non-empty run_id")
        if run_id in points_by_id:
            raise ValueError(f"duplicate run_id {run_id!r}")
        run_ids.append(run_id)
        points_by_id[run_id] = _clean_points(run.get("equity"))
        metadata[run_id] = {
            "instrument_id": run.get("instrument_id"),
            "strategy_hash": run.get("strategy_hash"),
            "data_version": run.get("data_version"),
            "run_hash": run.get("run_hash"),
            "requested_profile": run.get("requested_profile"),
            "effective_profile": run.get("effective_profile"),
            "metrics": run.get("metrics"),
        }

    all_days = sorted(
        {
            _utc_date(ts)
            for points in points_by_id.values()
            for ts, _ in points
        }
    )
    if not all_days:
        raise ValueError("no run carries usable equity history")

    intraday = len(all_days) < 3
    warnings: list[str] = []

    if intraday:
        aligned_ts = sorted(
            {ts for points in points_by_id.values() for ts, _ in points}
        )
        equity_by_id: dict[str, list[float]] = {}
        for run_id, points in points_by_id.items():
            values: list[float] = []
            pointer = 0
            current: float | None = None
            for ts in aligned_ts:
                while pointer < len(points) and points[pointer][0] <= ts:
                    current = points[pointer][1]
                    pointer += 1
                # ``current`` is None only before this run's first observation;
                # NaN (not a fabricated flat value) marks the inactive window.
                values.append(current if current is not None else math.nan)
            equity_by_id[run_id] = values
        warnings.append(
            "intraday_bar_returns: the runs span fewer than 3 UTC calendar "
            "days, so equity is aligned on bar timestamps and returns are "
            "bar-to-bar; Kelly sizing and annualisation are indicative only."
        )
    else:
        aligned_ts = [_day_start_ns(day) for day in all_days]
        equity_by_id = {}
        for run_id, points in points_by_id.items():
            if not points:
                equity_by_id[run_id] = [math.nan] * len(all_days)
                continue
            daily = dict(
                _daily_equity(
                    [
                        {"ts_ns": ts, "equity_usd": value}
                        for ts, value in points
                    ]
                )
            )
            values: list[float] = []
            current: float | None = None
            for day in all_days:
                if day in daily:
                    current = daily[day]
                # Days before this run's first observation stay NaN: the run
                # was not active, so it has no return to contribute.
                values.append(current if current is not None else math.nan)
            equity_by_id[run_id] = values

    returns_by_id = {
        run_id: _series_returns(equity_by_id[run_id]) for run_id in run_ids
    }

    # A sleeve whose aligned equity never moves has no measurable return
    # variation; inverse-volatility weighting would otherwise divide by the
    # volatility floor and hand it the whole book.
    reasons: dict[str, str] = {}
    weights_input: dict[str, list[float]] = {}
    for run_id in run_ids:
        values = returns_by_id[run_id]
        finite = [value for value in values if math.isfinite(value)]
        if len(finite) < 2:
            reasons[run_id] = "no usable equity history"
            warnings.append(
                f"excluded {run_id}: no usable equity history for portfolio returns."
            )
            continue
        if all(abs(value) <= 1e-15 for value in finite):
            reasons[run_id] = "flat equity curve (zero return variation)"
            warnings.append(
                f"excluded {run_id}: flat equity curve, so no volatility could "
                "be measured for weighting."
            )
            continue
        weights_input[run_id] = values

    weights = compute_weights(
        weights_input,
        method=method,
        max_weight=max_weight,
        vol_window=vol_window,
        min_periods=min_periods,
        correlation_penalty=correlation_penalty,
        max_pair_correlation=max_pair_correlation,
    )
    if method == "three_stage":
        allocation = three_stage_allocate(
            weights_input,
            max_weight=max_weight,
            vol_window=vol_window,
            min_periods=min_periods,
            correlation_penalty=correlation_penalty,
            max_pair_correlation=max_pair_correlation,
        )
        reasons.update(allocation["reasons"])
        reallocated_weight = float(allocation["reallocated"])
        duplicate_count = len(
            [reason for reason in allocation["reasons"].values() if reason.startswith("correlation >= ")]
        )
    else:
        reallocated_weight = 0.0
        duplicate_count = 0

    kelly_fractions = {
        run_id: kelly_stake_fraction(returns_by_id[run_id], config=config)
        for run_id in run_ids
    }

    correlation_matrix = {
        left: {
            right: (
                1.0
                if left == right
                else _correlation(returns_by_id[left], returns_by_id[right])
            )
            for right in run_ids
        }
        for left in run_ids
    }

    squared = sum(value * value for value in weights.values())
    effective_n = (1.0 / squared) if squared > 0.0 else 0.0
    active = [run_id for run_id in run_ids if weights.get(run_id, 0.0) > 0.0]
    max_observed_correlation = 0.0
    if len(active) >= 2:
        max_observed_correlation = max(
            correlation_matrix[left][right]
            for position, left in enumerate(active)
            for right in active[position + 1 :]
        )

    portfolio_returns: list[float] = []
    for index in range(len(aligned_ts) - 1):
        combined = 0.0
        for run_id in run_ids:
            weight = float(weights.get(run_id, 0.0))
            if weight == 0.0:
                continue
            value = returns_by_id[run_id][index]
            # NaN means the step precedes this sleeve's first observation, so
            # the sleeve is flat/uninvested there: 0.0 contribution, not a
            # fabricated return.
            if math.isfinite(value):
                combined += weight * value
        portfolio_returns.append(combined)

    equity_values = [float(bankroll_usd)]
    for value in portfolio_returns:
        equity_values.append(equity_values[-1] * (1.0 + value))

    equity_curve = [
        {
            "ts": _iso(ts),
            "ts_ns": ts,
            "equity_usd": equity_values[index],
        }
        for index, ts in enumerate(aligned_ts)
    ]
    equity_index = [
        {
            "ts": _iso(ts),
            "equity_index": equity_values[index] / bankroll_usd * 100.0,
        }
        for index, ts in enumerate(aligned_ts)
    ]

    final_equity = equity_values[-1]
    net_pnl = final_equity - bankroll_usd
    peak = equity_values[0]
    max_drawdown = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0.0:
            max_drawdown = min(max_drawdown, (value / peak - 1.0) * 100.0)

    metrics = {
        "starting_cash_usd": float(bankroll_usd),
        "final_equity_usd": final_equity,
        "net_pnl_usd": net_pnl,
        "return_pct": net_pnl / bankroll_usd * 100.0,
        "sharpe": _annualised_sharpe(portfolio_returns),
        "max_drawdown_pct": max_drawdown,
        "days": (all_days[-1] - all_days[0]).days + 1,
        "observations": len(portfolio_returns),
        "alignment": "intraday_bar_returns" if intraday else "daily_returns",
    }

    members: list[dict[str, Any]] = []
    for run_id in run_ids:
        info = metadata[run_id]
        run_returns = returns_by_id[run_id]
        run_metrics = info.get("metrics")
        net_pnl_run: float | None = None
        if isinstance(run_metrics, Mapping):
            candidate = run_metrics.get("net_pnl_usd")
            if isinstance(candidate, (int, float)) and math.isfinite(float(candidate)):
                net_pnl_run = float(candidate)
        if net_pnl_run is None and len(points_by_id[run_id]) >= 2:
            net_pnl_run = points_by_id[run_id][-1][1] - points_by_id[run_id][0][1]

        weight = float(weights.get(run_id, 0.0))
        drop_reason = reasons.get(run_id)
        if drop_reason is None and weight <= 0.0 and run_id in weights_input:
            drop_reason = f"zero weight under {method}"
        members.append(
            {
                "run_id": run_id,
                "instrument_id": info["instrument_id"],
                "strategy_hash": info["strategy_hash"],
                "data_version": info["data_version"],
                "run_hash": info["run_hash"],
                "requested_profile": info["requested_profile"],
                "effective_profile": info["effective_profile"],
                "weight": weight,
                "kelly_fraction": float(kelly_fractions[run_id]),
                "annualised_vol": _annualised_vol(run_returns),
                "sharpe": _annualised_sharpe(run_returns),
                "net_pnl_usd": net_pnl_run,
                "dropped": weight <= 0.0,
                "drop_reason": drop_reason,
            }
        )

    portfolio_kelly = kelly_stake_fraction(portfolio_returns, config=config)
    recommended_daily_stake = round(bankroll_usd * portfolio_kelly, 2)

    invested = sum(value for value in weights.values() if value > 0.0)
    residual = 1.0 - invested
    if residual > 1e-9:
        warnings.append(
            f"undeployed cash residual {residual * 100.0:.2f}%: "
            f"max_weight {max_weight:.2f} binds with {len(active)} active sleeve(s)."
        )
    if duplicate_count:
        warnings.append(
            f"collapsed {duplicate_count} near-duplicate sleeve(s) at "
            f"correlation >= {max_pair_correlation:.2f}."
        )
    if portfolio_kelly <= 0.0:
        warnings.append(
            "portfolio Kelly fraction is 0.00%: no positive lower-confidence-"
            "bound edge over the aligned return series."
        )

    return {
        "method": method,
        "provenance": PROVENANCE,
        "effective_n": float(effective_n),
        "max_pair_correlation": float(max_observed_correlation),
        "portfolio_kelly_fraction": float(portfolio_kelly),
        "recommended_daily_stake_usd": float(recommended_daily_stake),
        "reallocated_weight": reallocated_weight,
        "members": members,
        "weights": {run_id: float(weights.get(run_id, 0.0)) for run_id in run_ids},
        "kelly_fractions": {
            run_id: float(kelly_fractions[run_id]) for run_id in run_ids
        },
        "correlation_matrix": correlation_matrix,
        "metrics": metrics,
        "equity": equity_curve,
        "equity_index": equity_index,
        "methodology": "intraday_bar_returns" if intraday else "daily_returns",
        "warnings": warnings,
    }


__all__ = [
    "KellyConfig",
    "PORTFOLIO_METHODS",
    "PROVENANCE",
    "cap_weights",
    "compute_weights",
    "kelly_stake_fraction",
    "optimize_runs",
    "three_stage_allocate",
]
