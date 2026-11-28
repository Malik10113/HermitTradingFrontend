"""Instrument index: one row per outcome token per moneyline condition.

Polymarket treats YES and NO as separate assets, so one Nautilus
``BinaryOption`` is created per token.  The index joins the read-only market
metadata to the condition ids actually present in the fill tape, which is both
the moneyline filter and the coverage statement.

Token ids follow ``{market_slug}-YES.POLYMARKET`` / ``{market_slug}-NO.POLYMARKET``.
The index is cached under ``var/instruments/instruments.parquet``; rebuilding is
deterministic for the same inputs and duplicate conditions are resolved by
lexicographically smallest market id.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from backend import config
from backend.data import sources, versions
from backend.data._parquet import read_metadata, write_table
from backend.data.timeutil import as_utc, from_ns, seconds_to_ns

# Polymarket records a 0.001 tick for these binary markets.  The metadata file
# does not carry a tick-size column, so the venue constant is recorded here
# once (brief section 5) rather than guessed per row.
POLYMARKET_PRICE_PRECISION = 3
POLYMARKET_PRICE_TICK = 0.001

DATE_SUFFIX = re.compile(r"(\d{4}-\d{2}-\d{2})$")

INDEX_SCHEMA = pa.schema(
    [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("condition_id", pa.string(), nullable=False),
        pa.field("token_symbol", pa.string()),
        pa.field("token_index", pa.int8(), nullable=False),
        pa.field("market_id", pa.string()),
        pa.field("slug", pa.string()),
        pa.field("question", pa.string()),
        pa.field("league", pa.string()),
        pa.field("match_key", pa.string()),
        pa.field("event_date", pa.date32()),
        pa.field("home_name", pa.string()),
        pa.field("away_name", pa.string()),
        pa.field("selection", pa.string()),
        pa.field("price_precision", pa.int8(), nullable=False),
        pa.field("end_date_utc", pa.timestamp("us", tz="UTC")),
        pa.field("settlement_value", pa.float64()),
        pa.field("first_ts_utc", pa.timestamp("us", tz="UTC")),
        pa.field("last_ts_utc", pa.timestamp("us", tz="UTC")),
        pa.field("trade_count", pa.int64()),
        pa.field("tape_source", pa.string()),
    ]
)

_CACHE: dict[tuple[str, int], list[dict[str, Any]]] = {}


def _path(index_path: Path | None) -> Path:
    return Path(index_path) if index_path is not None else Path(config.INDEX_PATH)


def _tape_union_sql(paths: list[Path]) -> tuple[str, list[str]]:
    parts: list[str] = []
    params: list[str] = []
    for path in paths:
        parts.append(
            "SELECT condition_id, match_key, leg, timestamp, ? AS tape_source "
            "FROM read_parquet(?)"
        )
        params.extend([sources.relative_to_root(path), str(path)])
    return " UNION ALL ".join(parts), params


def _event_date_from_match_key(match_key: str | None):
    if not match_key:
        return None
    match = DATE_SUFFIX.search(match_key)
    if not match:
        return None
    from datetime import date

    return date.fromisoformat(match.group(1))


def _condition_rows() -> list[dict[str, Any]]:
    tapes = sources.tape_paths()
    markets_path = sources.require_file("polymarket_markets")
    results_path = sources.require_file("polymarket_results_markets")
    events_path = sources.require_file("polymarket_events")

    union_sql, union_params = _tape_union_sql(tapes)
    # The football tape and the EPL tape both carry the EPL conditions with
    # identical rows.  Counting the UNION ALL would double every EPL
    # ``trade_count`` and disagree with ``bars.py``/``quality.py``, which read a
    # single stored ``tape_source``.  Pick one deterministic source per
    # condition and count only that source's rows.
    query = f"""
        WITH raw_tape AS ({union_sql}),
        chosen_source AS (
            SELECT condition_id, min(tape_source) AS tape_source
            FROM raw_tape
            GROUP BY condition_id
        ),
        tape AS (
            SELECT
                r.condition_id,
                min(r.match_key) AS match_key,
                min(r.leg) AS leg,
                c.tape_source AS tape_source,
                min(r.timestamp) AS first_ts,
                max(r.timestamp) AS last_ts,
                count(*) AS trade_count
            FROM raw_tape AS r
            JOIN chosen_source AS c
              ON r.condition_id = c.condition_id AND r.tape_source = c.tape_source
            GROUP BY r.condition_id, c.tape_source
        ),
        markets AS (
            SELECT * FROM (
                SELECT
                    m.*,
                    row_number() OVER (
                        PARTITION BY m.condition_id ORDER BY m.id
                    ) AS rn
                FROM read_parquet(?) AS m
                JOIN tape AS t USING (condition_id)
                WHERE m.event_slug = t.match_key
                  AND starts_with(m.slug, m.event_slug || '-')
            )
            WHERE rn = 1
        ),
        results AS (
            SELECT
                market_id AS condition_id,
                max(CASE WHEN selection_index = 0 THEN settlement_value END) AS yes_settlement,
                max(CASE WHEN selection_index = 1 THEN settlement_value END) AS no_settlement,
                max(league) AS league,
                max(event_date) AS event_date,
                max(home_name) AS home_name,
                max(away_name) AS away_name,
                max(end_date) AS end_date
            FROM read_parquet(?)
            WHERE selection_index IN (0, 1)
              AND market_id IN (SELECT condition_id FROM tape)
            GROUP BY 1
        ),
        events AS (
            SELECT
                match_key,
                min(league) AS league,
                min(event_date) AS event_date,
                min(home_name) AS home_name,
                min(away_name) AS away_name
            FROM read_parquet(?)
            GROUP BY 1
        )
        SELECT
            t.condition_id,
            t.match_key,
            t.leg,
            t.tape_source,
            t.first_ts,
            t.last_ts,
            t.trade_count,
            m.id AS market_id,
            m.slug,
            m.question,
            m.token1,
            m.token2,
            m.end_date,
            COALESCE(r.league, e.league, split_part(t.match_key, '-', 1)) AS league,
            COALESCE(r.event_date, e.event_date) AS event_date,
            COALESCE(r.home_name, e.home_name) AS home_name,
            COALESCE(r.away_name, e.away_name) AS away_name,
            COALESCE(m.end_date, r.end_date) AS effective_end_date,
            r.yes_settlement,
            r.no_settlement
        FROM tape AS t
        JOIN markets AS m USING (condition_id)
        LEFT JOIN results AS r USING (condition_id)
        LEFT JOIN events AS e ON e.match_key = t.match_key
        ORDER BY t.match_key, t.leg, t.condition_id
    """
    params: list[Any] = [*union_params, str(markets_path), str(results_path), str(events_path)]
    con = duckdb.connect()
    try:
        table = con.sql(query, params=params).to_arrow_table()
    finally:
        con.close()
    return table.to_pylist()


def _token_rows(condition: dict[str, Any]) -> list[dict[str, Any]]:
    slug = condition["slug"]
    base = {
        "condition_id": condition["condition_id"],
        "market_id": condition.get("market_id"),
        "slug": slug,
        "question": condition.get("question"),
        "league": condition.get("league"),
        "match_key": condition.get("match_key"),
        "event_date": condition.get("event_date") or _event_date_from_match_key(condition.get("match_key")),
        "home_name": condition.get("home_name"),
        "away_name": condition.get("away_name"),
        "selection": condition.get("leg"),
        "price_precision": POLYMARKET_PRICE_PRECISION,
        "end_date_utc": as_utc(condition["effective_end_date"])
        if condition.get("effective_end_date")
        else None,
        "first_ts_utc": from_ns(seconds_to_ns(condition["first_ts"]))
        if condition.get("first_ts") is not None
        else None,
        "last_ts_utc": from_ns(seconds_to_ns(condition["last_ts"]))
        if condition.get("last_ts") is not None
        else None,
        "trade_count": int(condition["trade_count"]) if condition.get("trade_count") is not None else None,
        "tape_source": condition.get("tape_source"),
    }
    rows = []
    for token_index, suffix, symbol_key, settlement_key in (
        (0, "YES", "token1", "yes_settlement"),
        (1, "NO", "token2", "no_settlement"),
    ):
        row = dict(base)
        row.update(
            {
                "instrument_id": f"{slug}-{suffix}.POLYMARKET",
                "token_symbol": condition.get(symbol_key),
                "token_index": token_index,
                "settlement_value": condition.get(settlement_key),
            }
        )
        rows.append(row)
    return rows


def refresh_index(index_path: Path | None = None) -> dict[str, Any]:
    """Rebuild the instrument index from the read-only sources."""
    destination = _path(index_path)
    conditions = _condition_rows()
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        rows.extend(_token_rows(condition))
    rows.sort(
        key=lambda row: (
            row.get("match_key") or "",
            row.get("selection") or "",
            row["token_index"],
            row["instrument_id"],
        )
    )
    table = pa.Table.from_pylist(rows, schema=INDEX_SCHEMA)
    version = versions.instrument_index_version()
    write_table(
        table,
        destination,
        {
            "instrument_index_version": version,
            "builder_version": versions.INSTRUMENT_INDEX_BUILDER_VERSION,
            "source": "polymarket/sii/markets.parquet + moneyline tapes",
            "row_count": table.num_rows,
            "conditions": len(conditions),
        },
    )
    _CACHE.clear()
    return {
        "path": destination,
        "rows": table.num_rows,
        "conditions": len(conditions),
        "instrument_index_version": version,
    }


def load_index(index_path: Path | None = None, *, refresh: bool = True) -> list[dict[str, Any]]:
    """Load the index, building it on first use unless ``refresh=False``."""
    destination = _path(index_path)
    if refresh and not destination.exists():
        refresh_index(destination)
    if not destination.exists():
        raise FileNotFoundError(
            f"instrument index is absent at {destination}; run "
            "`python -c 'from backend.data.instruments import refresh_index; "
            "refresh_index()'` first."
        )
    stat = destination.stat()
    key = (str(destination), stat.st_mtime_ns)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rows = pq.read_table(destination).to_pylist()
    for row in rows:
        for field in ("first_ts_utc", "last_ts_utc", "end_date_utc"):
            if row.get(field) is not None:
                row[field] = as_utc(row[field])
    _CACHE.clear()
    _CACHE[key] = rows
    return rows


def _event_date_value(value: Any) -> "date | None":
    """Normalise a stored ``event_date`` to a date for range comparisons."""
    from datetime import date, datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def row_venue(row: dict[str, Any]) -> str | None:
    """The venue encoded in the instrument id, e.g. ``POLYMARKET`` -> polymarket.

    A row without a venue suffix has no venue rather than an assumed one.
    """
    instrument_id = str(row.get("instrument_id") or "")
    if "." not in instrument_id:
        return None
    suffix = instrument_id.rsplit(".", 1)[-1].strip().lower()
    return suffix or None


def _matches(
    row: dict[str, Any],
    *,
    needle: str,
    league_filter: str | None,
    venue_filter: str | None,
    event_date: "date | None",
    date_from: "date | None",
    date_to: "date | None",
) -> bool:
    if league_filter and (row.get("league") or "").lower() != league_filter:
        return False
    if venue_filter and row_venue(row) != venue_filter:
        return False
    if event_date is not None or date_from is not None or date_to is not None:
        observed = _event_date_value(row.get("event_date"))
        # A date filter cannot match an unknown date: the index has no value
        # to compare, and guessing one would violate the no-invented-data rule.
        if observed is None:
            return False
        if event_date is not None and observed != event_date:
            return False
        if date_from is not None and observed < date_from:
            return False
        if date_to is not None and observed > date_to:
            return False
    if needle:
        haystack = " ".join(
            str(row.get(field) or "")
            for field in (
                "question",
                "slug",
                "match_key",
                "home_name",
                "away_name",
                "selection",
                "instrument_id",
            )
        ).lower()
        if not all(token in haystack for token in needle.split()):
            return False
    return True


def _matching_rows(
    q: str | None,
    league: str | None,
    *,
    index_path: Path | None,
    venue: str | None,
    event_date: Any,
    date_from: Any,
    date_to: Any,
) -> list[dict[str, Any]]:
    from datetime import date

    rows = load_index(index_path)
    needle = (q or "").strip().lower()
    league_filter = league.strip().lower() if league else None
    venue_filter = venue.strip().lower() if venue else None
    on_date = event_date if isinstance(event_date, date) else _event_date_value(event_date)
    lower = date_from if isinstance(date_from, date) else _event_date_value(date_from)
    upper = date_to if isinstance(date_to, date) else _event_date_value(date_to)
    matches = [
        row
        for row in rows
        if _matches(
            row,
            needle=needle,
            league_filter=league_filter,
            venue_filter=venue_filter,
            event_date=on_date,
            date_from=lower,
            date_to=upper,
        )
    ]
    def sort_key(row: dict[str, Any]) -> tuple[int, str, str, int, str]:
        observed = _event_date_value(row.get("event_date"))
        return (
            -(observed.toordinal()) if observed else 0,
            row.get("match_key") or "",
            row.get("selection") or "",
            row["token_index"],
            row.get("instrument_id") or "",
        )

    matches.sort(key=sort_key)
    return matches


def search(
    q: str | None = None,
    league: str | None = None,
    limit: int | None = 25,
    offset: int = 0,
    *,
    index_path: Path | None = None,
    venue: str | None = None,
    event_date: Any = None,
    date_from: Any = None,
    date_to: Any = None,
) -> list[dict[str, Any]]:
    """Deterministic substring search across market, team and match fields.

    Filters are conjunctive: ``q``, ``league``, ``venue``, an exact
    ``event_date`` and/or an inclusive ``date_from``/``date_to`` range over the
    stored ``event_date``. A row with no ``event_date`` never matches a date
    filter. ``limit=None`` returns every match so a caller can report the true
    total before paging.
    """
    if (limit is not None and limit < 0) or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    matches = _matching_rows(
        q,
        league,
        index_path=index_path,
        venue=venue,
        event_date=event_date,
        date_from=date_from,
        date_to=date_to,
    )
    return matches[offset:] if limit is None else matches[offset : offset + limit]


def count(
    q: str | None = None,
    league: str | None = None,
    *,
    index_path: Path | None = None,
    venue: str | None = None,
    event_date: Any = None,
    date_from: Any = None,
    date_to: Any = None,
) -> int:
    """True number of rows matching the same filters as :func:`search`."""
    return len(
        _matching_rows(
            q,
            league,
            index_path=index_path,
            venue=venue,
            event_date=event_date,
            date_from=date_from,
            date_to=date_to,
        )
    )


def get_instrument(
    instrument_id: str, *, index_path: Path | None = None
) -> dict[str, Any] | None:
    for row in load_index(index_path):
        if row["instrument_id"] == instrument_id:
            return row
    return None


def instrument_for(
    condition_id: str, token_index: int, *, index_path: Path | None = None
) -> dict[str, Any] | None:
    for row in load_index(index_path):
        if row["condition_id"] == condition_id and row["token_index"] == token_index:
            return row
    return None


def pair_for(
    instrument_id: str, *, index_path: Path | None = None
) -> dict[str, Any] | None:
    """The sibling YES/NO instrument for a condition, or ``None``."""
    row = get_instrument(instrument_id, index_path=index_path)
    if row is None:
        return None
    sibling_index = 1 if row["token_index"] == 0 else 0
    return instrument_for(row["condition_id"], sibling_index, index_path=index_path)


def index_version(index_path: Path | None = None) -> str:
    return read_metadata(_path(index_path)).get("instrument_index_version", "")


def condition_ids(index_path: Path | None = None) -> list[str]:
    seen: dict[str, None] = {}
    for row in load_index(index_path):
        seen.setdefault(row["condition_id"], None)
    return list(seen)
