"""Data-quality preflight for an instrument and range.

The quality report is descriptive, never a profitability claim.  It reports the
available period, trade count, active-minute percentage, longest no-trade gap,
median trade size, bar count and a documented A/B/C/D grade, plus warnings the
UI must show before a run.

Grade thresholds (all conditions must hold, checked A → B → C; otherwise D):

===========  ===========  ================  =========================
Grade        Trades       Active minutes    Longest no-trade gap
===========  ===========  ================  =========================
A            ≥ 2000       ≥ 60%             ≤ 120 minutes
B            ≥ 500        ≥ 30%             ≤ 480 minutes
C            ≥ 100        ≥ 10%             ≤ 1440 minutes
D            anything else
===========  ===========  ================  =========================

``active_minutes_pct`` is active minutes divided by every minute between the
first and last trade in the range.  ``longest_gap_minutes`` counts consecutive
minutes *without* a trade, i.e. the delta between adjacent active minutes minus
one.  Warm-up never changes the grade; it produces a warning.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb

from backend.data import instruments, settlement, sources, versions
from backend.data.timeutil import from_ns, isoformat_z, to_ns

GRADE_THRESHOLDS: dict[str, dict[str, float]] = {
    "A": {"min_trades": 2000, "min_active_pct": 60.0, "max_gap_minutes": 120},
    "B": {"min_trades": 500, "min_active_pct": 30.0, "max_gap_minutes": 480},
    "C": {"min_trades": 100, "min_active_pct": 10.0, "max_gap_minutes": 1440},
}

SPARSE_ACTIVE_PCT = GRADE_THRESHOLDS["B"]["min_active_pct"]
LONG_GAP_MINUTES = GRADE_THRESHOLDS["B"]["max_gap_minutes"]
VERY_FEW_TRADES = GRADE_THRESHOLDS["C"]["min_trades"]


def grade_for(
    trade_count: int,
    active_minutes_pct: float,
    longest_gap_minutes: float,
) -> str:
    """Return the data-quality grade for already-computed coverage stats."""
    for grade in ("A", "B", "C"):
        threshold = GRADE_THRESHOLDS[grade]
        if (
            trade_count >= threshold["min_trades"]
            and active_minutes_pct >= threshold["min_active_pct"]
            and longest_gap_minutes <= threshold["max_gap_minutes"]
        ):
            return grade
    return "D"


def _seconds(value: datetime | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_ns(value) // 1_000_000_000
    raw = int(value)
    return raw // 1_000_000_000 if raw > 10**12 else raw


def _trade_stats(
    tape_source: str,
    condition_id: str,
    start_seconds: int | None,
    end_seconds: int | None,
) -> dict[str, Any]:
    tape = sources.path_from_relative(tape_source)
    if not tape.exists():
        raise sources.MissingDatasetError(sources.dataset_name_for(tape), tape)
    clauses = ["condition_id = ?"]
    params: list[Any] = [condition_id]
    if start_seconds is not None:
        clauses.append("timestamp >= ?")
        params.append(start_seconds)
    if end_seconds is not None:
        clauses.append("timestamp < ?")
        params.append(end_seconds)
    where = " AND ".join(clauses)
    con = duckdb.connect()
    try:
        base = con.execute(
            f"""
            SELECT
                count(*) AS trade_count,
                min(timestamp) AS first_ts,
                max(timestamp) AS last_ts,
                median(usd_amount) AS median_usd,
                count(DISTINCT floor(timestamp / 60)) AS active_minutes
            FROM read_parquet(?)
            WHERE {where}
            """,
            [str(tape), *params],
        ).fetchone()
        gaps = con.execute(
            f"""
            WITH minutes AS (
                SELECT DISTINCT floor(timestamp / 60)::BIGINT AS minute
                FROM read_parquet(?)
                WHERE {where}
            ),
            ordered AS (
                SELECT minute, lag(minute) OVER (ORDER BY minute) AS previous
                FROM minutes
            )
            SELECT max(minute - previous) FROM ordered
            """,
            [str(tape), *params],
        ).fetchone()
    finally:
        con.close()
    trade_count, first_ts, last_ts, median_usd, active_minutes = base
    longest_delta = gaps[0] if gaps else None
    span_minutes = (
        int((int(last_ts) - int(first_ts)) // 60) + 1
        if first_ts is not None and last_ts is not None
        else 0
    )
    active_pct = (
        (float(active_minutes) / float(span_minutes)) * 100.0 if span_minutes else 0.0
    )
    longest_gap = max(int(longest_delta) - 1, 0) if longest_delta is not None else 0
    return {
        "trade_count": int(trade_count or 0),
        "first_ts": int(first_ts) if first_ts is not None else None,
        "last_ts": int(last_ts) if last_ts is not None else None,
        "median_trade_size_usd": float(median_usd) if median_usd is not None else None,
        "active_minutes": int(active_minutes or 0),
        "span_minutes": span_minutes,
        "active_minutes_pct": active_pct,
        "longest_gap_minutes": longest_gap,
    }


def quality(
    instrument_id: str,
    start: datetime | int | None = None,
    end: datetime | int | None = None,
    warmup_bars: int | None = None,
    *,
    index_path=None,
    settlement_path=None,
) -> dict[str, Any]:
    """Quality report for ``instrument_id`` between ``start`` (inclusive) and
    ``end`` (exclusive).  Unknown values are ``None``, never zero-filled."""
    row = instruments.get_instrument(instrument_id, index_path=index_path)
    if row is None:
        raise KeyError(f"unknown instrument {instrument_id!r}")
    start_seconds = _seconds(start)
    end_seconds = _seconds(end)
    stats = _trade_stats(
        row["tape_source"], row["condition_id"], start_seconds, end_seconds
    )

    first_dt = from_ns(stats["first_ts"] * 1_000_000_000) if stats["first_ts"] else None
    last_dt = from_ns(stats["last_ts"] * 1_000_000_000) if stats["last_ts"] else None
    bar_count = stats["active_minutes"]

    settlement_row = settlement.get_settlement(row["condition_id"], path=settlement_path)
    settlement_value = (
        settlement_row.value_for(int(row["token_index"])) if settlement_row else None
    )

    grade = (
        "D"
        if stats["trade_count"] == 0
        else grade_for(
            stats["trade_count"],
            stats["active_minutes_pct"],
            float(stats["longest_gap_minutes"]),
        )
    )

    warnings: list[dict[str, str]] = []
    if stats["trade_count"] == 0:
        warnings.append(
            {
                "code": "no_data",
                "message": "No trades in the selected range; no bars exist.",
            }
        )
    else:
        if stats["active_minutes_pct"] < SPARSE_ACTIVE_PCT:
            warnings.append(
                {
                    "code": "sparse_coverage",
                    "message": (
                        f"Only {stats['active_minutes_pct']:.1f}% of minutes have a "
                        f"trade (threshold {SPARSE_ACTIVE_PCT:.0f}%)."
                    ),
                }
            )
        if stats["longest_gap_minutes"] > LONG_GAP_MINUTES:
            warnings.append(
                {
                    "code": "long_no_trade_gap",
                    "message": (
                        f"Longest no-trade gap is {stats['longest_gap_minutes']} "
                        f"minutes (threshold {LONG_GAP_MINUTES:.0f})."
                    ),
                }
            )
        if stats["trade_count"] < VERY_FEW_TRADES:
            warnings.append(
                {
                    "code": "very_few_trades",
                    "message": (
                        f"Only {stats['trade_count']} trades (threshold "
                        f"{VERY_FEW_TRADES:.0f})."
                    ),
                }
            )
    if settlement_row is None or settlement_value is None:
        warnings.append(
            {
                "code": "missing_settlement",
                "message": (
                    "No published settlement value for this token; a run that holds "
                    "to expiry cannot settle it."
                ),
            }
        )
    if warmup_bars is not None and bar_count < warmup_bars:
        warnings.append(
            {
                "code": "insufficient_warmup",
                "message": (
                    f"{bar_count} bars available but {warmup_bars} are required for "
                    "indicator warm-up."
                ),
            }
        )

    return {
        "instrument_id": instrument_id,
        "condition_id": row["condition_id"],
        "token_index": int(row["token_index"]),
        "available_from_utc": isoformat_z(first_dt) if first_dt else None,
        "available_to_utc": isoformat_z(last_dt) if last_dt else None,
        "span_minutes": stats["span_minutes"],
        "trade_count": stats["trade_count"],
        "active_minutes": stats["active_minutes"],
        "active_minutes_pct": round(stats["active_minutes_pct"], 3),
        "longest_gap_minutes": stats["longest_gap_minutes"],
        "median_trade_size_usd": stats["median_trade_size_usd"],
        "bar_count": bar_count,
        "quality_grade": grade,
        "settlement_known": settlement_value is not None,
        "settlement_value": settlement_value,
        "warnings": warnings,
        "data_version": versions.tape_data_version(),
    }
