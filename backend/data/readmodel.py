"""DuckDB read model over ``var/bars``.

Only cached bar files are queried; the API never computes indicator values or
touches the sibling data from this module.  Returned timestamps are ISO-8601
UTC strings with ``_ns`` companions, and the payload always carries the data
version that produced the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from backend import config
from backend.data import bars
from backend.data._parquet import read_metadata
from backend.data.timeutil import isoformat_z

DEFAULT_LIMIT = 20_000
MAX_LIMIT = 200_000


def _bar_path(instrument_id: str, alias: str) -> tuple[Path, str]:
    alias_name = bars._normalise_alias(alias)
    return Path(config.BARS_DIR) / instrument_id / f"{alias_name}.parquet", alias_name


def _ensure_file(instrument_id: str, alias: str) -> tuple[Path, str]:
    path, alias_name = _bar_path(instrument_id, alias)
    if not path.exists():
        info = bars.ensure_bars_file(instrument_id, alias_name)
        path = info["path"]
    return path, alias_name


def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    ts_event = int(row["ts_event"])
    ts_init = int(row["ts_init"])
    return {
        "instrument_id": row["instrument_id"],
        "bar_type": row["bar_type"],
        "ts_event": isoformat_z(ts_event),
        "ts_event_ns": ts_event,
        "ts_init": isoformat_z(ts_init),
        "ts_init_ns": ts_init,
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
        "volume_usd": row["volume"],
        "trade_count": row["trade_count"],
        "source": row["source"],
        "data_version": row["data_version"],
    }


def load_bars(
    instrument_id: str,
    alias: str = "1m",
    start_ns: int | None = None,
    end_ns: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return one page of bars plus provenance.

    Response shape follows ``GET /api/bars`` in ``docs/IMPLEMENTATION.md``:
    ``{instrument_id, bar_type, data_version, bars[], has_more}``.  ``start_ns``
    is inclusive, ``end_ns`` exclusive.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    page_limit = DEFAULT_LIMIT if limit is None else min(int(limit), MAX_LIMIT)
    path, _alias = _ensure_file(instrument_id, alias)
    meta = read_metadata(path)
    bar_type = meta.get("bar_type") or bars.parse_bar_alias(alias, instrument_id)
    data_version = meta.get("data_version", "")

    conditions = ["1 = 1"]
    params: list[Any] = [str(path)]
    if start_ns is not None:
        conditions.append("ts_event >= ?")
        params.append(int(start_ns))
    if end_ns is not None:
        conditions.append("ts_event < ?")
        params.append(int(end_ns))
    params.append(page_limit + 1)
    query = f"""
        SELECT instrument_id, bar_type, ts_event, ts_init, open, high, low, close,
               volume, trade_count, source, data_version
        FROM read_parquet(?)
        WHERE {' AND '.join(conditions)}
        ORDER BY ts_event
        LIMIT ?
    """
    con = duckdb.connect()
    try:
        rows = con.execute(query, params).fetchall()
    finally:
        con.close()
    has_more = len(rows) > page_limit
    rows = rows[:page_limit]
    columns = [
        "instrument_id",
        "bar_type",
        "ts_event",
        "ts_init",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "source",
        "data_version",
    ]
    items = [_row_to_api(dict(zip(columns, row))) for row in rows]
    return {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "data_version": data_version or (items[0]["data_version"] if items else ""),
        "bars": items,
        "has_more": has_more,
    }


def bar_bounds(instrument_id: str, alias: str = "1m") -> dict[str, Any]:
    """First/last cached bar timestamp and the bar count."""
    path, alias_name = _ensure_file(instrument_id, alias)
    con = duckdb.connect()
    try:
        first, last, count = con.execute(
            "SELECT min(ts_event), max(ts_event), count(*) FROM read_parquet(?)",
            [str(path)],
        ).fetchone()
    finally:
        con.close()
    meta = read_metadata(path)
    return {
        "instrument_id": instrument_id,
        "bar_type": meta.get("bar_type") or bars.parse_bar_alias(alias, instrument_id),
        "alias": alias_name,
        "first_ts_utc": isoformat_z(int(first)) if first is not None else None,
        "first_ts_ns": int(first) if first is not None else None,
        "last_ts_utc": isoformat_z(int(last)) if last is not None else None,
        "last_ts_ns": int(last) if last is not None else None,
        "bar_count": int(count),
        "data_version": meta.get("data_version", ""),
    }


def bar_coverage(
    instrument_id: str,
    alias: str = "1m",
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> dict[str, Any]:
    """Coverage of a bar file over an optional range, in bars and percent."""
    path, alias_name = _ensure_file(instrument_id, alias)
    conditions = ["1 = 1"]
    params: list[Any] = [str(path)]
    if start_ns is not None:
        conditions.append("ts_event >= ?")
        params.append(int(start_ns))
    if end_ns is not None:
        conditions.append("ts_event < ?")
        params.append(int(end_ns))
    con = duckdb.connect()
    try:
        first, last, count = con.execute(
            f"SELECT min(ts_event), max(ts_event), count(*) FROM read_parquet(?) "
            f"WHERE {' AND '.join(conditions)}",
            params,
        ).fetchone()
    finally:
        con.close()
    alias_info = bars.TIME_ALIASES.get(alias_name)
    coverage_pct: float | None = None
    expected = None
    if first is not None and alias_info is not None and int(count) > 0:
        period_ns = alias_info[2] * 1_000_000_000
        expected = (int(last) - int(first)) // period_ns + 1
        coverage_pct = round(int(count) / expected * 100.0, 3)
    return {
        "instrument_id": instrument_id,
        "alias": alias_name,
        "bar_type": bars.parse_bar_alias(alias_name, instrument_id),
        "bar_count": int(count),
        "expected_bars": int(expected) if expected is not None else None,
        "coverage_pct": coverage_pct,
        "first_ts_utc": isoformat_z(int(first)) if first is not None else None,
        "last_ts_utc": isoformat_z(int(last)) if last is not None else None,
        "data_version": read_metadata(path).get("data_version", ""),
    }


def data_version_for(instrument_id: str, alias: str = "1m") -> str:
    path, _ = _bar_path(instrument_id, alias)
    if not path.exists():
        return ""
    return read_metadata(path).get("data_version", "")


def available_aliases(instrument_id: str) -> list[str]:
    directory = Path(config.BARS_DIR) / instrument_id
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.parquet"))


def read_cached_table(instrument_id: str, alias: str = "1m"):
    """Raw Arrow table for indicator/engine consumers that need columns."""
    path, _ = _bar_path(instrument_id, alias)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached {alias!r} bars for {instrument_id!r}; build them first"
        )
    return pq.read_table(path)
