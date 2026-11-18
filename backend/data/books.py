"""Book coverage probe.

v1 does not replay L2 books; it only reports whether snapshots exist for an
instrument's match and month, so the Book profile can fall back honestly.
The compact book tree is partitioned by ``year_month=YYYY-MM``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.config import BOOKS_DIR


def book_coverage(instrument_id: str, match_key: str | None = None) -> dict[str, Any]:
    if match_key is None:
        try:
            from backend.data.instruments import get_instrument

            row = get_instrument(instrument_id)
            match_key = row.get("match_key") if isinstance(row, dict) else getattr(row, "match_key", None)
        except Exception:  # noqa: BLE001
            match_key = None
    if not BOOKS_DIR.exists():
        return {"has_coverage": False, "reason": "no_book_coverage", "window": None, "match_key": match_key}
    months = sorted(p.name.split("=", 1)[1] for p in BOOKS_DIR.glob("year_month=*") if p.is_dir())
    if not months:
        return {"has_coverage": False, "reason": "no_book_coverage", "window": None, "match_key": match_key}
    # Coverage is reported at month granularity; a specific match may still have
    # no rows, in which case the run-level fallback still applies.
    return {
        "has_coverage": True,
        "reason": None,
        "window": {"first_month": months[0], "last_month": months[-1], "partitions": len(months)},
        "match_key": match_key,
        "source": str(BOOKS_DIR),
    }


def latest_snapshot(condition_id: str, ts_ns: int) -> dict[str, Any] | None:
    """Return the latest L2 snapshot at or before ``ts_ns``.

    The compact store is not indexed for point lookups in v1; return None so the
    execution model uses its recorded Conservative fallback.  This function is
    the single seam for the v2 book replay.
    """
    return None
