"""Bar builder: 1-minute, aggregated time bars and non-time bars.

Bars are built from Polymarket fills and stored under
``var/bars/{instrument_id}/{alias}.parquet`` with the canonical schema:

``instrument_id, bar_type, ts_event, ts_init, open, high, low, close, volume,
trade_count, source, data_version``

Rules enforced here:

* one token per instrument; YES uses ``price_yes``, NO uses ``1 - price_yes``;
* fills are ordered by ``(timestamp, log_index, transaction_hash)``;
* an empty minute (or higher-timeframe bucket) produces **no bar**;
* nothing is forward-filled and no synthetic bar is ever emitted;
* timestamps are UTC nanoseconds; ``ts_init`` is the bar close;
* higher timeframes are aggregated from cached 1-minute bars only.

Non-time bars: ``tick_N`` closes every N fills, ``volume_N`` every N contracts,
``value_N`` every N USD of notional, and ``renko_X`` on every observed move of
at least X in probability.  ``volume`` remains USD notional in all cases.  A
trailing partial tick/volume/value bucket is kept because it contains observed
fills; renko emits no partial brick.

Caching is keyed by ``data_version + builder_version + bar_type`` and stored in
the Parquet metadata.  The Nautilus bar-type string is the model's canonical
rendering for the pinned 2.0 release; the documentation's inner ``-LAST`` is
also accepted on input and normalised.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb
import pyarrow as pa

from nautilus_trader.model import (
    AggregationSource,
    BarAggregation,
    BarSpecification,
    BarType,
    InstrumentId,
    PriceType,
)

from backend import config
from backend.data import instruments, sources, versions
from backend.data._parquet import read_metadata, rows_to_table, write_table
from backend.data.timeutil import NS_PER_SECOND, isoformat_z, to_ns

CANONICAL_SCHEMA = pa.schema(
    [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("bar_type", pa.string(), nullable=False),
        pa.field("ts_event", pa.int64(), nullable=False),
        pa.field("ts_init", pa.int64(), nullable=False),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("trade_count", pa.int64()),
        pa.field("source", pa.string(), nullable=False),
        pa.field("data_version", pa.string(), nullable=False),
    ]
)

_AGGREGATION_FROM_NAME = {
    "MINUTE": BarAggregation.MINUTE,
    "HOUR": BarAggregation.HOUR,
    "DAY": BarAggregation.DAY,
    "TICK": BarAggregation.TICK,
    "VOLUME": BarAggregation.VOLUME,
    "VALUE": BarAggregation.VALUE,
    "RENKO": BarAggregation.RENKO,
}
_PRICE_TYPE_FROM_NAME = {"LAST": PriceType.LAST}

TIME_ALIASES: dict[str, tuple[int, BarAggregation, int]] = {
    # alias: (step, aggregation, period seconds)
    "1m": (1, BarAggregation.MINUTE, 60),
    "5m": (5, BarAggregation.MINUTE, 5 * 60),
    "15m": (15, BarAggregation.MINUTE, 15 * 60),
    "1h": (1, BarAggregation.HOUR, 60 * 60),
    "1d": (1, BarAggregation.DAY, 24 * 60 * 60),
}
TIME_ALIAS_ORDER = ("1m", "5m", "15m", "1h", "1d")

NON_TIME_PATTERN = re.compile(r"^(tick|volume|value)_(\d+)$")
RENKO_PATTERN = re.compile(r"^renko_(\d+(?:\.\d+)?)$")

DEFAULT_ALIAS = "1m"
NON_TIME_ALIASES = ("tick_100", "volume_1000", "value_10000", "renko_0.01")

BUILDER = versions.BUILDER_VERSION
_DOCS_COMPOSITE_LAST = "@1-MINUTE-LAST-EXTERNAL"
_NAUTILUS_COMPOSITE_LAST = "@1-MINUTE-EXTERNAL"


def bar_type_alias(bar_type: str) -> str:
    """Map a full Nautilus bar-type string to its semantic file alias."""
    text = bar_type.strip()
    if text in TIME_ALIASES:
        return text
    if text in NON_TIME_ALIASES:
        return text
    _, _, venue_and_spec = text.rpartition(".")
    venue, _, spec = venue_and_spec.partition("-")
    if not venue or not spec:
        raise ValueError(f"not a Nautilus bar type string: {bar_type!r}")
    spec = spec.replace(_DOCS_COMPOSITE_LAST, _NAUTILUS_COMPOSITE_LAST)
    head = spec.split("@", 1)[0]
    parts = head.split("-")
    if len(parts) != 4:
        raise ValueError(f"cannot parse bar specification {spec!r}")
    step = int(parts[0])
    unit = parts[1]
    if unit == "MINUTE" and step in (1, 5, 15) and "@" not in spec:
        return f"{step}m"
    if unit == "MINUTE" and "@" in spec and step in (1, 5, 15):
        return f"{step}m"
    if unit == "HOUR" and step == 1:
        return "1h"
    if unit == "DAY" and step == 1:
        return "1d"
    if unit in ("TICK", "VOLUME", "VALUE"):
        return f"{unit.lower()}_{step}"
    if unit == "RENKO":
        brick = step * instruments.POLYMARKET_PRICE_TICK
        return f"renko_{brick:g}"
    raise ValueError(f"unsupported bar type {bar_type!r}")


def _spec_from_text(text: str) -> tuple[int, BarAggregation, AggregationSource, str]:
    parts = text.split("-")
    if len(parts) == 3:
        # composite inner spec omits the implicit LAST price type
        parts = [parts[0], parts[1], "LAST", parts[2]]
    if len(parts) != 4:
        raise ValueError(f"cannot parse bar specification {text!r}")
    step = int(parts[0])
    try:
        aggregation = _AGGREGATION_FROM_NAME[parts[1]]
    except KeyError as exc:
        raise ValueError(f"unknown bar aggregation {parts[1]!r}") from exc
    if parts[2] not in _PRICE_TYPE_FROM_NAME:
        raise ValueError(f"unsupported price type {parts[2]!r}; only LAST is supported")
    try:
        source = AggregationSource.from_str(parts[3])
    except Exception as exc:  # noqa: BLE001 - normalise the model's error
        raise ValueError(f"unknown aggregation source {parts[3]!r}") from exc
    return step, aggregation, source, parts[3]


def _build_bar_type(instrument_id: str, alias: str) -> BarType:
    try:
        iid = InstrumentId.from_str(instrument_id)
    except Exception as exc:  # noqa: BLE001 - normalise the model's error
        raise ValueError(f"invalid instrument_id {instrument_id!r}") from exc

    if alias in TIME_ALIASES:
        step, aggregation, _ = TIME_ALIASES[alias]
        spec = BarSpecification(step, aggregation, PriceType.LAST)
        if alias == "1m":
            return BarType(iid, spec, AggregationSource.EXTERNAL)
        return BarType.new_composite(
            iid,
            spec,
            AggregationSource.INTERNAL,
            1,
            BarAggregation.MINUTE,
            AggregationSource.EXTERNAL,
        )

    non_time = NON_TIME_PATTERN.match(alias)
    if non_time:
        unit, raw_step = non_time.groups()
        step = int(raw_step)
        if step <= 0:
            raise ValueError(f"{alias!r}: step must be positive")
        aggregation = {
            "tick": BarAggregation.TICK,
            "volume": BarAggregation.VOLUME,
            "value": BarAggregation.VALUE,
        }[unit]
        spec = BarSpecification(step, aggregation, PriceType.LAST)
        return BarType(iid, spec, AggregationSource.EXTERNAL)

    renko = RENKO_PATTERN.match(alias)
    if renko:
        brick = float(renko.group(1))
        if brick <= 0:
            raise ValueError(f"{alias!r}: brick size must be positive")
        ticks = int(round(brick / instruments.POLYMARKET_PRICE_TICK))
        if ticks <= 0:
            raise ValueError(f"{alias!r}: brick is smaller than the price tick")
        spec = BarSpecification(ticks, BarAggregation.RENKO, PriceType.LAST)
        return BarType(iid, spec, AggregationSource.EXTERNAL)

    raise ValueError(
        f"unknown bar alias {alias!r}; expected one of {list(TIME_ALIASES)} or "
        "tick_N, volume_N, value_N, renko_X"
    )


def parse_bar_alias(alias: str, instrument_id: str) -> str:
    """Return the full Nautilus bar-type string for ``alias``.

    Accepts the semantic aliases (``1m``…``1d``, ``tick_100``, ``renko_0.01``)
    and an already-full bar-type string, including the documentation form with
    an inner ``-LAST`` (normalised to the pinned model's canonical rendering).
    """
    raw = (alias or "").strip()
    if not raw:
        raise ValueError("bar alias is empty")
    if raw in TIME_ALIASES or NON_TIME_PATTERN.match(raw) or RENKO_PATTERN.match(raw):
        return str(_build_bar_type(instrument_id, raw.lower()))
    spec = raw
    if not spec.startswith(instrument_id + "-"):
        raise ValueError(
            f"bar type {raw!r} does not belong to instrument {instrument_id!r}"
        )
    spec = spec[len(instrument_id) + 1 :]
    spec = spec.replace(_DOCS_COMPOSITE_LAST, _NAUTILUS_COMPOSITE_LAST)
    head, at, tail = spec.partition("@")
    step, aggregation, source, _ = _spec_from_text(head)
    if not at:
        if source is not AggregationSource.EXTERNAL:
            raise ValueError(f"standard bar type must be external: {raw!r}")
        spec_obj = BarSpecification(step, aggregation, PriceType.LAST)
        return str(
            BarType(InstrumentId.from_str(instrument_id), spec_obj, AggregationSource.EXTERNAL)
        )
    if source is not AggregationSource.INTERNAL:
        raise ValueError(f"composite bar type must be internal: {raw!r}")
    inner_step, inner_aggregation, inner_source, _ = _spec_from_text(tail)
    if (inner_step, inner_aggregation) != (1, BarAggregation.MINUTE):
        raise ValueError("only 1-minute external composites are supported")
    spec_obj = BarSpecification(step, aggregation, PriceType.LAST)
    return str(
        BarType.new_composite(
            InstrumentId.from_str(instrument_id),
            spec_obj,
            AggregationSource.INTERNAL,
            inner_step,
            inner_aggregation,
            inner_source,
        )
    )


def _bars_path(instrument_id: str, alias: str) -> Path:
    return Path(config.BARS_DIR) / instrument_id / f"{alias}.parquet"


def bar_type_object(instrument_id: str, alias: str) -> BarType:
    """Return the Nautilus ``BarType`` object for an alias or full string."""
    alias_name = _normalise_alias(alias)
    return _build_bar_type(instrument_id, alias_name)


def normalise_alias(alias: str) -> str:
    """Public alias normaliser used by the read model and catalog builder."""
    return _normalise_alias(alias)


def _tape_path(relative: str) -> Path:
    path = sources.path_from_relative(relative)
    if not path.exists():
        raise sources.MissingDatasetError(sources.dataset_name_for(path), path)
    return path


def _one_minute_rows(row: dict[str, Any], data_version: str) -> list[dict[str, Any]]:
    tape = _tape_path(row["tape_source"])
    con = duckdb.connect()
    try:
        result = con.execute(
            """
            WITH fills AS (
                SELECT
                    timestamp,
                    log_index,
                    transaction_hash,
                    price_yes,
                    usd_amount
                FROM read_parquet(?)
                WHERE condition_id = ?
            ),
            ordered AS (
                SELECT
                    floor(timestamp / 60)::BIGINT AS bucket,
                    *,
                    row_number() OVER (
                        PARTITION BY floor(timestamp / 60)
                        ORDER BY timestamp, log_index, transaction_hash
                    ) AS rn,
                    row_number() OVER (
                        PARTITION BY floor(timestamp / 60)
                        ORDER BY timestamp DESC, log_index DESC, transaction_hash DESC
                    ) AS rrn
                FROM fills
            )
            SELECT
                bucket,
                max(CASE WHEN rn = 1 THEN price_yes END) AS open,
                max(price_yes) AS high,
                min(price_yes) AS low,
                max(CASE WHEN rrn = 1 THEN price_yes END) AS close,
                sum(usd_amount) AS volume,
                count(*) AS trade_count
            FROM ordered
            GROUP BY bucket
            ORDER BY bucket
            """,
            [str(tape), row["condition_id"]],
        ).fetchall()
    finally:
        con.close()

    token_index = int(row["token_index"])
    bar_type = parse_bar_alias(DEFAULT_ALIAS, row["instrument_id"])
    out: list[dict[str, Any]] = []
    for bucket, open_, high, low, close, volume, count in result:
        price_open = float(open_)
        price_high = float(high)
        price_low = float(low)
        price_close = float(close)
        if token_index == 1:
            price_open, price_high, price_low, price_close = (
                1.0 - price_open,
                1.0 - price_low,
                1.0 - price_high,
                1.0 - price_close,
            )
        ts_event = int(bucket) * 60 * NS_PER_SECOND
        out.append(
            {
                "instrument_id": row["instrument_id"],
                "bar_type": bar_type,
                "ts_event": ts_event,
                "ts_init": ts_event + 60 * NS_PER_SECOND,
                "open": round(price_open, 9),
                "high": round(price_high, 9),
                "low": round(price_low, 9),
                "close": round(price_close, 9),
                "volume": float(volume or 0.0),
                "trade_count": int(count),
                "source": "polymarket",
                "data_version": data_version,
            }
        )
    return out


def _aggregate(rows: list[dict[str, Any]], alias: str, instrument_id: str, data_version: str) -> list[dict[str, Any]]:
    _step, _aggregation, period_seconds = TIME_ALIASES[alias]
    if not rows:
        return []
    bar_type = parse_bar_alias(alias, instrument_id)
    period_ns = period_seconds * NS_PER_SECOND
    out: list[dict[str, Any]] = []
    current_bucket: int | None = None
    bucket_rows: list[dict[str, Any]] = []

    def flush(bucket: int) -> None:
        ordered = bucket_rows
        out.append(
            {
                "instrument_id": instrument_id,
                "bar_type": bar_type,
                "ts_event": bucket * period_ns,
                "ts_init": (bucket + 1) * period_ns,
                "open": ordered[0]["open"],
                "high": max(item["high"] for item in ordered),
                "low": min(item["low"] for item in ordered),
                "close": ordered[-1]["close"],
                "volume": sum(item["volume"] for item in ordered),
                "trade_count": sum(item["trade_count"] for item in ordered),
                "source": "polymarket",
                "data_version": data_version,
            }
        )

    for row in rows:
        bucket = int(row["ts_event"]) // period_ns
        if current_bucket is None:
            current_bucket = bucket
        if bucket != current_bucket:
            flush(current_bucket)
            bucket_rows = []
            current_bucket = bucket
        bucket_rows.append(row)
    if current_bucket is not None:
        flush(current_bucket)
    return out


def _fill_rows(row: dict[str, Any]) -> list[tuple[int, float, float, float]]:
    """(timestamp ns, token price, usd notional, contracts) in fill order."""
    tape = _tape_path(row["tape_source"])
    con = duckdb.connect()
    try:
        result = con.execute(
            """
            SELECT timestamp, price_yes, usd_amount, token_amount
            FROM read_parquet(?)
            WHERE condition_id = ?
            ORDER BY timestamp, log_index, transaction_hash
            """,
            [str(tape), row["condition_id"]],
        ).fetchall()
    finally:
        con.close()
    token_index = int(row["token_index"])
    fills: list[tuple[int, float, float, float]] = []
    for timestamp, price_yes, usd, contracts in result:
        price = float(price_yes)
        if token_index == 1:
            price = 1.0 - price
        fills.append(
            (
                int(timestamp) * NS_PER_SECOND,
                round(price, 9),
                float(usd or 0.0),
                float(contracts or 0.0),
            )
        )
    return fills


def _finish_non_time(
    instrument_id: str,
    alias: str,
    data_version: str,
    *,
    ts_open: int,
    ts_close: int,
    open_: float,
    close: float,
    high: float,
    low: float,
    volume: float,
    trade_count: int,
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "bar_type": parse_bar_alias(alias, instrument_id),
        "ts_event": int(ts_open),
        "ts_init": int(ts_close),
        "open": round(open_, 9),
        "high": round(high, 9),
        "low": round(low, 9),
        "close": round(close, 9),
        "volume": float(volume),
        "trade_count": int(trade_count),
        "source": "polymarket",
        "data_version": data_version,
    }


def _tick_bars(row: dict[str, Any], alias: str, step: int, data_version: str) -> list[dict[str, Any]]:
    fills = _fill_rows(row)
    out: list[dict[str, Any]] = []
    chunk: list[tuple[int, float, float, float]] = []
    for fill in fills:
        chunk.append(fill)
        if len(chunk) == step:
            out.append(_bar_from_chunk(row["instrument_id"], alias, data_version, chunk))
            chunk = []
    if chunk:
        out.append(_bar_from_chunk(row["instrument_id"], alias, data_version, chunk))
    return out


def _bar_from_chunk(
    instrument_id: str, alias: str, data_version: str, chunk: list[tuple[int, float, float, float]]
) -> dict[str, Any]:
    prices = [item[1] for item in chunk]
    return _finish_non_time(
        instrument_id,
        alias,
        data_version,
        ts_open=chunk[0][0],
        ts_close=chunk[-1][0],
        open_=prices[0],
        close=prices[-1],
        high=max(prices),
        low=min(prices),
        volume=sum(item[2] for item in chunk),
        trade_count=len(chunk),
    )


def _threshold_bars(
    row: dict[str, Any], alias: str, threshold: float, field: int, data_version: str
) -> list[dict[str, Any]]:
    fills = _fill_rows(row)
    out: list[dict[str, Any]] = []
    chunk: list[tuple[int, float, float, float]] = []
    accumulated = 0.0
    for fill in fills:
        chunk.append(fill)
        accumulated += fill[field]
        if accumulated >= threshold:
            out.append(_bar_from_chunk(row["instrument_id"], alias, data_version, chunk))
            chunk = []
            accumulated = 0.0
    if chunk:
        out.append(_bar_from_chunk(row["instrument_id"], alias, data_version, chunk))
    return out


def _renko_bars(
    row: dict[str, Any], alias: str, brick: float, data_version: str
) -> list[dict[str, Any]]:
    """One bar per observed move of at least ``brick``.

    The bar opens at the previous brick close and closes at the brick boundary
    nearest the observed price.  A quiet tail with no brick-sized move emits no
    bar (and therefore loses no invented price).  ``high``/``low`` include the
    observed prices inside the move.
    """
    fills = _fill_rows(row)
    out: list[dict[str, Any]] = []
    if not fills:
        return out
    open_price = fills[0][1]
    close_price = open_price
    chunk: list[tuple[int, float, float, float]] = []
    observed_high = open_price
    observed_low = open_price

    for fill in fills:
        price = fill[1]
        observed_high = max(observed_high, price)
        observed_low = min(observed_low, price)
        chunk.append(fill)
        move = price - close_price
        # Tolerance absorbs binary-float round-off at an exact brick boundary.
        if abs(move) + 1e-9 < brick:
            continue
        levels = int((abs(move) + 1e-9) // brick)
        if levels < 1:
            levels = 1
        direction = 1.0 if move > 0 else -1.0
        new_close = close_price + direction * levels * brick
        out.append(
            _finish_non_time(
                row["instrument_id"],
                alias,
                data_version,
                ts_open=chunk[0][0],
                ts_close=fill[0],
                open_=close_price,
                close=new_close,
                high=observed_high,
                low=observed_low,
                volume=sum(item[2] for item in chunk),
                trade_count=len(chunk),
            )
        )
        close_price = new_close
        chunk = []
        observed_high = new_close
        observed_low = new_close
    return out


def _build_rows(row: dict[str, Any], alias: str, data_version: str) -> list[dict[str, Any]]:
    if alias in TIME_ALIASES:
        if alias == "1m":
            return _one_minute_rows(row, data_version)
        # Higher frames are aggregated from the cached 1-minute bars only.
        import pyarrow.parquet as pq

        base_info = ensure_bars_file(row["instrument_id"], "1m")
        base = pq.read_table(base_info["path"]).to_pylist()
        return _aggregate(base, alias, row["instrument_id"], data_version)

    non_time = NON_TIME_PATTERN.match(alias)
    if non_time:
        unit, raw_step = non_time.groups()
        step = int(raw_step)
        if unit == "tick":
            return _tick_bars(row, alias, step, data_version)
        if unit == "volume":
            return _threshold_bars(row, alias, float(step), 3, data_version)
        return _threshold_bars(row, alias, float(step), 2, data_version)

    renko = RENKO_PATTERN.match(alias)
    if renko:
        return _renko_bars(row, alias, float(renko.group(1)), data_version)

    raise ValueError(f"unsupported bar alias {alias!r}")


def _normalise_alias(alias: str) -> str:
    text = (alias or "").strip()
    if text in TIME_ALIASES or text in NON_TIME_ALIASES:
        return text
    if NON_TIME_PATTERN.match(text):
        return text
    if RENKO_PATTERN.match(text):
        return text
    return bar_type_alias(text)


def ensure_bars_file(instrument_id: str, alias: str = DEFAULT_ALIAS) -> dict[str, Any]:
    """Build and cache one bar file; return its path and provenance."""
    row = instruments.get_instrument(instrument_id)
    if row is None:
        raise KeyError(f"unknown instrument {instrument_id!r}; refresh the instrument index")
    alias_name = _normalise_alias(alias)
    bar_type = parse_bar_alias(alias_name, instrument_id)
    data_version = versions.tape_data_version()
    path = _bars_path(instrument_id, alias_name)

    if path.exists():
        meta = read_metadata(path)
        if (
            meta.get("data_version") == data_version
            and meta.get("builder_version") == BUILDER
            and meta.get("bar_type") == bar_type
            and meta.get("alias") == alias_name
        ):
            return {
                "path": path,
                "rows": int(meta.get("row_count", 0)),
                "bar_type": bar_type,
                "alias": alias_name,
                "data_version": data_version,
            }

    rows = _build_rows(row, alias_name, data_version)
    table = rows_to_table(rows, CANONICAL_SCHEMA)
    write_table(
        table,
        path,
        {
            "instrument_id": instrument_id,
            "alias": alias_name,
            "bar_type": bar_type,
            "data_version": data_version,
            "builder_version": BUILDER,
            "source": "polymarket",
            "row_count": table.num_rows,
            "non_time": "true" if alias_name not in TIME_ALIASES else "false",
            "aggregation_chain": "1m" if alias_name in ("5m", "15m", "1h", "1d") else "fills",
            "condition_id": row["condition_id"],
            "token_index": int(row["token_index"]),
        },
    )
    return {
        "path": path,
        "rows": table.num_rows,
        "bar_type": bar_type,
        "alias": alias_name,
        "data_version": data_version,
    }


def build_bars(
    instrument_id: str,
    alias: str = DEFAULT_ALIAS,
    start: datetime | int | None = None,
    end: datetime | int | None = None,
) -> list[dict[str, Any]]:
    """Return cached/on-demand bars in the canonical shape.

    ``start`` is inclusive and ``end`` exclusive; datetimes must be aware UTC.
    """
    info = ensure_bars_file(instrument_id, alias)
    import pyarrow.parquet as pq

    table = pq.read_table(info["path"])
    rows = table.to_pylist()
    start_ns = _bound_ns(start)
    end_ns = _bound_ns(end)
    if start_ns is not None:
        rows = [row for row in rows if int(row["ts_event"]) >= start_ns]
    if end_ns is not None:
        rows = [row for row in rows if int(row["ts_event"]) < end_ns]
    return rows


def _bound_ns(value: datetime | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_ns(value)
    return int(value)


def build_all_time_bars(instrument_id: str) -> dict[str, int]:
    """Build 1m and every derived time frame; return alias -> row count."""
    out: dict[str, int] = {}
    for alias in TIME_ALIAS_ORDER:
        out[alias] = ensure_bars_file(instrument_id, alias)["rows"]
    return out


def bar_rows(rows: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Serialise canonical bars with ISO timestamps for API responses."""
    for row in rows:
        item = dict(row)
        item["ts_event"] = isoformat_z(int(row["ts_event"]))
        item["ts_init"] = isoformat_z(int(row["ts_init"]))
        yield item
