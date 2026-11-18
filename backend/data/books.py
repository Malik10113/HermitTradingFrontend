"""L2 book coverage and snapshots.

The compact store is partitioned by ``year_month`` and ``league``.  v1 uses it
only for the Book execution profile: a run is Book only when the condition has
snapshots, and each fill uses the latest snapshot at or before the decision
time.  No queue position is modelled; the walk is a bounded estimate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from backend.config import BOOKS_DIR

_PREVIOUS_MONTHS = 3


def _month_key(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m")
    text = str(value)
    return text[:7] if len(text) >= 7 else None


def _shift_month(month: str, back: int) -> str:
    year, mon = (int(x) for x in month.split("-"))
    total = year * 12 + (mon - 1) - back
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _files(league: str | None, event_date: Any) -> list[Path]:
    if not BOOKS_DIR.exists():
        return []
    months: list[str] = []
    month = _month_key(event_date)
    if month:
        months = [_shift_month(month, n) for n in range(_PREVIOUS_MONTHS + 1)]
    files: list[Path] = []
    for path in sorted(BOOKS_DIR.glob("year_month=*/league=*/data_*.parquet")):
        year_month = path.parent.parent.name.split("=", 1)[1]
        file_league = path.parent.name.split("=", 1)[1]
        if league and file_league != league:
            continue
        if months and year_month not in months:
            continue
        files.append(path)
    return files


def _row_for(instrument_id: str) -> dict[str, Any] | None:
    try:
        from backend.data.instruments import get_instrument

        return get_instrument(instrument_id)
    except Exception:  # noqa: BLE001
        return None


def book_coverage(instrument_id: str, match_key: str | None = None) -> dict[str, Any]:
    row = _row_for(instrument_id)
    if row is None:
        return {"has_coverage": False, "reason": "unknown_instrument", "window": None, "match_key": match_key}
    files = _files(row.get("league"), row.get("event_date"))
    if not files:
        return {"has_coverage": False, "reason": "no_book_coverage", "window": None, "match_key": row.get("match_key")}
    side = "token1" if int(row.get("token_index") or 0) == 0 else "token2"
    paths = [str(p) for p in files]
    con = duckdb.connect()
    try:
        query = (
            "SELECT count(*), min(timestamp_ms), max(timestamp_ms) FROM read_parquet(?) "
            "WHERE condition_id = ? AND side = ?"
        )
        count, first_ms, last_ms = con.execute(query, [paths, row["condition_id"], side]).fetchone()
    except Exception:  # noqa: BLE001
        return {"has_coverage": False, "reason": "book_scan_failed", "window": None, "match_key": row.get("match_key")}
    finally:
        con.close()
    if not count:
        return {"has_coverage": False, "reason": "no_book_coverage", "window": None, "match_key": row.get("match_key")}
    from datetime import datetime, timezone

    start_ns = int(first_ms) * 1_000_000
    end_ns = int(last_ms) * 1_000_000
    return {
        "has_coverage": True,
        "reason": None,
        "window": {
            "start_ns": start_ns,
            "end_ns": end_ns,
            "from": datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "to": datetime.fromtimestamp(end_ns / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "snapshots": int(count),
            "months": sorted({p.parent.parent.name.split("=", 1)[1] for p in files}),
        },
        "match_key": row.get("match_key"),
        "source": str(BOOKS_DIR),
    }


def _levels(raw: Any) -> list[tuple[float, float]]:
    if raw is None:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[float, float]] = []
    for level in data or []:
        try:
            price = float(level["price"])
            size = float(level["size"])
        except Exception:  # noqa: BLE001
            continue
        if 0.0 < price < 1.0 and size > 0.0:
            out.append((price, size))
    return out


class CompactBookProvider:
    """Latest snapshot at or before the decision time for one token."""

    def __init__(self, instrument_id: str) -> None:
        from backend.engine.execution import BookSnapshot

        self._snapshot_cls = BookSnapshot
        row = _row_for(instrument_id)
        if row is None:
            raise ValueError(f"unknown instrument {instrument_id!r}")
        self.condition_id = row["condition_id"]
        self.side = "token1" if int(row.get("token_index") or 0) == 0 else "token2"
        self.league = row.get("league")
        self.event_date = row.get("event_date")
        self._files = [str(p) for p in _files(self.league, self.event_date)]
        self._con = duckdb.connect()
        self._cache: dict[int, Any] = {}

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:  # noqa: BLE001
            pass

    def get_snapshot(self, condition_id: str, ts_ns: int):
        if not self._files:
            return None
        ts_ms = int(ts_ns) // 1_000_000
        query = """
            SELECT timestamp_ms, bids, asks, best_bid, best_ask
            FROM read_parquet(?)
            WHERE condition_id = ? AND side = ? AND timestamp_ms <= ?
            ORDER BY timestamp_ms DESC
            LIMIT 1
        """
        try:
            row = self._con.execute(query, [self._files, condition_id, self.side, ts_ms]).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        timestamp_ms, bids, asks, best_bid, best_ask = row
        return self._snapshot_cls(
            condition_id=condition_id,
            ts_ns=int(timestamp_ms) * 1_000_000,
            bids=_levels(bids),
            asks=_levels(asks),
        )


def latest_snapshot(condition_id: str, ts_ns: int):
    """Compatibility seam; the runner uses CompactBookProvider per run."""
    return None
