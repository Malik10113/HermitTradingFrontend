"""Shared helpers for indicator tests."""

from __future__ import annotations

import math
from typing import Sequence

Values = Sequence[float | None]


def leading_none_count(values: Values) -> int:
    count = 0
    for value in values:
        if value is not None:
            break
        count += 1
    return count


def assert_values_close(
    actual: Values,
    expected: Values,
    *,
    context: str = "series",
    rel_tol: float = 1e-9,
    abs_tol: float = 1e-9,
) -> None:
    """Compare two series, treating ``None`` as an exact expected state."""
    assert len(actual) == len(expected), (
        f"{context}: length {len(actual)} != expected {len(expected)}"
    )
    for index, (got, want) in enumerate(zip(actual, expected)):
        if want is None:
            assert got is None, f"{context}[{index}]: expected None, got {got!r}"
            continue
        assert got is not None, f"{context}[{index}]: expected {want!r}, got None"
        assert isinstance(got, float), (
            f"{context}[{index}]: expected float, got {type(got).__name__}"
        )
        assert math.isclose(got, want, rel_tol=rel_tol, abs_tol=abs_tol), (
            f"{context}[{index}]: {got!r} != {want!r} "
            f"(diff {abs(got - want)!r})"
        )
