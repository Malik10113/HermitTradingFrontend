"""Adapters between the HTTP layer and the domain packages.

The domain packages own the work; this module keeps the routes thin and
normalises polars/pandas/list return types into JSON-ready dicts.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from backend.api.errors import ApiError, bad_request, not_found, unavailable
from backend.config import code_version, nautilus_version
from backend.data.sources import MissingDatasetError
from backend.data.versions import run_data_version
from backend.engine.artifacts import parquet_rows, read_json_artifact
from backend.jobs import state


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(v) if not isinstance(v, dict) else v for v in value]
    if isinstance(value, dict):
        if "items" in value:
            return list(value["items"])
        return [value]
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict(orient="records")
        except TypeError:
            return value.to_dict()
    return []


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e17:
            v /= 1e9
        return datetime.fromtimestamp(v, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _ns(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v > 10**17 else v * 1_000_000_000
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return None


def _parse_date(value: str | None, end: bool = False) -> int | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError as exc:
        raise bad_request("bad_date", "dates must be ISO-8601 (YYYY-MM-DD)", {"value": value}) from exc
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    ns = int(dt.timestamp() * 1_000_000_000)
    return ns + (86_400_000_000_000 - 1 if end else 0)


DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# v1 reads Polymarket only. Kalshi and Betfair are v2 (AGENTS.md section 4),
# so an unknown venue is rejected rather than silently treated as "all".
V1_VENUES: tuple[str, ...] = ("polymarket",)

# The P&L basis is encoded next to the numbers so a client never has to infer
# whether gross includes fees. Values are definitions, not new measurements.
METRIC_SEMANTICS: dict[str, str] = {
    "gross_pnl_usd": (
        "Sum of (executed exit price - executed entry price) * quantity. "
        "Slippage and spread are embedded in the fill prices; fees are excluded."
    ),
    "net_pnl_usd": (
        "Gross P&L less fees_usd. Slippage and spread remain embedded in the "
        "fill prices; they are not subtracted twice."
    ),
    "gross_return_pct": "gross_pnl_usd / starting capital * 100.",
    "net_return_pct": "net_pnl_usd / starting capital * 100.",
    "fees_usd": (
        "Venue fees charged as a separate cash line. Included in net P&L and "
        "excluded from gross P&L."
    ),
    "slippage_usd": (
        "Executed price minus the reference price, in USD. Already embedded in "
        "gross and net fill prices; reported separately, never added again."
    ),
    "cost_bps_of_notional": (
        "(fees_usd + slippage_usd) / entry notional * 10,000. A cost measure, "
        "not a return."
    ),
    "settlement_pnl_usd": "Net P&L of positions held to settlement.",
    "trading_pnl_usd": "Net P&L of positions closed before settlement.",
}


def _filter_date(value: str | None, name: str) -> date | None:
    """Strict ``YYYY-MM-DD`` validation for an instrument-filter date."""
    if value is None or value == "":
        return None
    if not DATE_PATTERN.fullmatch(value):
        raise bad_request(
            "bad_date",
            f"{name} must be an ISO-8601 date (YYYY-MM-DD)",
            {"field": name, "value": value},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise bad_request(
            "bad_date",
            f"{name} is not a valid calendar date",
            {"field": name, "value": value},
        ) from exc


def _venue(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    venue = value.strip().lower()
    if venue not in V1_VENUES:
        raise bad_request(
            "bad_venue",
            f"venue {value!r} is not available in v1",
            {"field": "venue", "value": value, "supported": list(V1_VENUES)},
        )
    return venue


def _instrument_filters(
    venue: str | None,
    event_date: str | None,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    """Validate the shared instrument/search filters once.

    ``date`` is an exact event date; ``from``/``to`` are an inclusive range over
    ``event_date``. Mixing an exact date with a range is ambiguous and rejected.
    """
    filters = {
        "venue": _venue(venue),
        "event_date": _filter_date(event_date, "date"),
        "date_from": _filter_date(start, "from"),
        "date_to": _filter_date(end, "to"),
    }
    if filters["event_date"] is not None and (
        filters["date_from"] is not None or filters["date_to"] is not None
    ):
        raise bad_request(
            "ambiguous_date_filter",
            "use either date or from/to, not both",
            {"fields": ["date", "from", "to"]},
        )
    if (
        filters["date_from"] is not None
        and filters["date_to"] is not None
        and filters["date_from"] > filters["date_to"]
    ):
        raise bad_request(
            "bad_date_range",
            "from must be on or before to",
            {"from": start, "to": end},
        )
    return filters


def instrument_catalog() -> dict[str, Any] | None:
    """Coverage computed from the stored instrument index, or ``None``.

    This deliberately reports only what the index contains. It does not claim a
    bar count or a book-snapshot window, which are not derivable here.
    """
    from backend.data import instruments

    try:
        rows = instruments.load_index(refresh=False)
    except Exception:  # noqa: BLE001 - a missing index is "not available"
        return None
    firsts = [row.get("first_ts_utc") for row in rows if row.get("first_ts_utc") is not None]
    lasts = [row.get("last_ts_utc") for row in rows if row.get("last_ts_utc") is not None]
    return {
        "instruments": len(rows),
        "source": "var/instruments/instruments.parquet",
        "coverage_from": _iso(min(firsts)) if firsts else None,
        "coverage_to": _iso(max(lasts)) if lasts else None,
        "coverage_basis": "first/last fill timestamp in the stored instrument index",
    }


def meta() -> dict[str, Any]:
    lifecycle_version = None
    try:
        from backend.data.lifecycle import lifecycle_version as lv

        lifecycle_version = lv()
    except Exception:  # noqa: BLE001
        pass
    return {
        "code_version": code_version(),
        "nautilus_version": nautilus_version(),
        "data_version": run_data_version(),
        "lifecycle_version": lifecycle_version,
        "catalog": instrument_catalog(),
    }


# ------------------------------------------------------------------ instruments


def _index_rows() -> list[dict[str, Any]]:
    from backend.data import instruments

    try:
        rows = _records(instruments.load_index())
    except FileNotFoundError:
        rows = []
    if not rows:
        refresh = getattr(instruments, "refresh_index", None)
        if refresh is not None:
            try:
                refresh()
                rows = _records(instruments.load_index())
            except Exception as exc:  # noqa: BLE001
                raise unavailable("index_unavailable", f"instrument index is not built: {exc}") from exc
    return rows


def list_instruments(
    q: str | None,
    league: str | None,
    limit: int,
    offset: int,
    *,
    venue: str | None = None,
    event_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Filter the instrument inventory and report the true match count.

    The page is limited; ``total`` is the number of rows matching the filters,
    never the page length, so the UI cannot mistake a page for the inventory.
    """
    from backend.data import instruments

    filters = _instrument_filters(venue, event_date, start, end)
    search = getattr(instruments, "search", None)
    count = getattr(instruments, "count", None)
    if search is not None and count is not None:
        out = search(
            q=q,
            league=league,
            limit=limit,
            offset=offset,
            **filters,
        )
        total = count(q=q, league=league, **filters)
        items = [serialize_instrument(r) for r in _records(out)]
        return {"items": items, "instruments": items, "total": int(total)}
    rows = _index_rows()
    needle = (q or "").strip().lower()
    if league:
        rows = [r for r in rows if str(r.get("league", "")).lower() == league.lower()]
    if filters["venue"]:
        rows = [r for r in rows if instruments.row_venue(r) == filters["venue"]]
    if any(filters[key] is not None for key in ("event_date", "date_from", "date_to")):
        kept = []
        for row in rows:
            observed = instruments._event_date_value(row.get("event_date"))  # noqa: SLF001
            if observed is None:
                continue
            if filters["event_date"] is not None and observed != filters["event_date"]:
                continue
            if filters["date_from"] is not None and observed < filters["date_from"]:
                continue
            if filters["date_to"] is not None and observed > filters["date_to"]:
                continue
            kept.append(row)
        rows = kept
    if needle:
        rows = [
            r
            for r in rows
            if needle in json.dumps({k: r.get(k) for k in ("question", "market_slug", "match_key", "home_name", "away_name", "instrument_id")}, default=str).lower()
        ]
    total = len(rows)
    items = [serialize_instrument(r) for r in rows[offset : offset + limit]]
    return {"items": items, "instruments": items, "total": total}


def serialize_instrument(row: dict[str, Any]) -> dict[str, Any]:
    from backend.data.instruments import row_venue

    return {
        "instrument_id": row.get("instrument_id"),
        "venue": row_venue(row),
        "condition_id": row.get("condition_id"),
        "market_id": row.get("market_id"),
        "market_slug": row.get("market_slug") or row.get("slug"),
        "token": row.get("token") or row.get("token_symbol"),
        "token_index": row.get("token_index"),
        "question": row.get("question"),
        "league": row.get("league"),
        "match_key": row.get("match_key"),
        "event_date": _iso(row.get("event_date")),
        "home_name": row.get("home_name"),
        "away_name": row.get("away_name"),
        "selection": row.get("selection"),
        "price_precision": row.get("price_precision", 3),
        "end_date": _iso(row.get("end_date_utc") or row.get("end_date")),
        "settlement_value": row.get("settlement_value"),
        "first_ts": _iso(row.get("first_ts_utc") or row.get("first_ts")),
        "last_ts": _iso(row.get("last_ts_utc") or row.get("last_ts")),
        "coverage": {
            "from": _iso(row.get("first_ts_utc") or row.get("first_ts")),
            "to": _iso(row.get("last_ts_utc") or row.get("last_ts")),
        },
        "trade_count": row.get("trade_count"),
        "bar_count": row.get("bar_count"),
        "settlement_known": row.get("settlement_value") in (0, 1, 0.0, 1.0),
    }


def get_instrument(instrument_id: str) -> dict[str, Any]:
    from backend.data import instruments

    row = None
    getter = getattr(instruments, "get_instrument", None)
    if getter is not None:
        try:
            row = getter(instrument_id)
        except Exception:  # noqa: BLE001
            row = None
    if row is None:
        for r in _index_rows():
            if r.get("instrument_id") == instrument_id:
                row = r
                break
    if row is None:
        raise not_found("instrument", instrument_id)
    out = serialize_instrument(dict(row))
    pair = getattr(instruments, "pair_for", None)
    if pair is not None:
        try:
            p = pair(instrument_id)
            if p:
                out["complement_instrument_id"] = (
                    p.get("instrument_id") if isinstance(p, dict) else getattr(p, "instrument_id", None)
                )
        except Exception:  # noqa: BLE001
            pass
    out["complement_instrument_id"] = out.get("complement_instrument_id") or (
        instrument_id.replace("-YES.POLYMARKET", "-NO.POLYMARKET")
        if instrument_id.endswith("-YES.POLYMARKET")
        else instrument_id.replace("-NO.POLYMARKET", "-YES.POLYMARKET")
    )
    return out


# -------------------------------------------------------------------------- bars


def _bar_alias(alias: str) -> str:
    return alias or "1m"


def ensure_bars(instrument_id: str, alias: str, start_ns: int | None = None, end_ns: int | None = None) -> None:
    from backend.data import bars, readmodel

    try:
        bars.parse_bar_alias(alias, instrument_id)
    except (ValueError, KeyError) as exc:
        raise bad_request(
            "bad_bar_type",
            str(exc),
            {"path": "bar_type", "bar_type": alias, "instrument_id": instrument_id},
        ) from exc
    loader = getattr(readmodel, "load_bars", None)
    if loader is not None:
        try:
            got = loader(instrument_id, alias, start_ns, end_ns, 1)
            if isinstance(got, dict) and got.get("bars"):
                return
        except Exception:  # noqa: BLE001
            pass
    build = getattr(bars, "build_bars", None)
    if build is None:
        return
    try:
        build(instrument_id=instrument_id, alias=alias)
    except TypeError:
        try:
            build(instrument_id, alias)
        except MissingDatasetError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise bad_request("bars_build_failed", str(exc), {"instrument_id": instrument_id, "bar_type": alias}) from exc
    except MissingDatasetError:
        # A registered dataset is absent: 503 from the app handler, never a
        # silent fallback to another source (AGENTS.md section 4).
        raise
    except FileNotFoundError as exc:
        raise not_found("instrument data", instrument_id) from exc


def bars(instrument_id: str, alias: str, start: str | None, end: str | None, limit: int) -> dict[str, Any]:
    start_ns, end_ns = _parse_date(start), _parse_date(end, end=True)
    from backend.data import readmodel

    get_instrument(instrument_id)
    ensure_bars(instrument_id, alias, start_ns, end_ns)
    loader = getattr(readmodel, "load_bars", None)
    rows: list[dict[str, Any]] = []
    page: dict[str, Any] = {}
    if loader is not None:
        try:
            page = loader(instrument_id, alias, start_ns, end_ns, limit)
            rows = _records(page.get("bars")) if isinstance(page, dict) else _records(page)
        except Exception as exc:  # noqa: BLE001
            raise unavailable("bars_unavailable", str(exc)) from exc
    out = []
    for r in rows[:limit]:
        ts = r.get("ts_event_ns") or _ns(r.get("ts_event"))
        out.append(
            {
                "ts_event": _iso(ts),
                "ts_event_ns": ts,
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume_usd": r.get("volume"),
                "trade_count": r.get("trade_count"),
                "is_stale": bool(r.get("is_stale", False)),
            }
        )
    return {
        "instrument_id": instrument_id,
        "bar_type": page.get("bar_type") if page else (rows[0].get("bar_type") if rows else _bar_alias(alias)),
        "data_version": page.get("data_version") if page else (rows[0].get("data_version") if rows else run_data_version()),
        "bars": out,
        "has_more": bool(page.get("has_more")) if isinstance(page, dict) else False,
    }


# ------------------------------------------------------------------ indicators


def indicator_registry() -> dict[str, Any]:
    from backend.indicators.registry import registry_json

    return registry_json()


def indicator_preview(body: dict[str, Any]) -> dict[str, Any]:
    from backend.data import readmodel
    from backend.indicators.compute import compute_series
    from backend.indicators.registry import validate_params
    from backend.rules.schema import IndicatorInstance

    instrument_id = body.get("instrument_id")
    if not instrument_id:
        raise bad_request("missing_instrument", "instrument_id is required")
    alias = body.get("bar_type", "1m")
    start_ns, end_ns = _parse_date(body.get("from")), _parse_date(body.get("to"), end=True)
    try:
        ensure_bars(instrument_id, alias, start_ns, end_ns)
        # Compute over the whole series so the cache key (data version, bar
        # type, spec) is window-independent and warm-up precedes ``from``.
        page = readmodel.load_bars(instrument_id, alias, None, None, 200_000)
    except ApiError:
        raise
    except KeyError as exc:
        # The bar builder names an unknown instrument; that is a lookup miss.
        raise not_found("instrument", instrument_id) from exc
    except MissingDatasetError:
        # Absent registered dataset: 503 from the app handler, never a
        # different source (AGENTS.md section 4).
        raise
    except FileNotFoundError as exc:
        raise bad_request(
            "bars_unavailable", str(exc), {"instrument_id": instrument_id, "bar_type": alias}
        ) from exc
    except ValueError as exc:
        raise bad_request(
            "bars_unavailable", str(exc), {"instrument_id": instrument_id, "bar_type": alias}
        ) from exc
    rows = _records(page.get("bars")) if isinstance(page, dict) else _records(page)
    instances = []
    for index, raw in enumerate(body.get("indicators", [])):
        try:
            inst = IndicatorInstance.model_validate(raw)
            validate_params(inst.type, inst.params)
        except Exception as exc:  # noqa: BLE001 - both carry a field path
            raise bad_request(
                "bad_indicator",
                str(exc),
                {"path": f"indicators.{index}", "indicator": raw if isinstance(raw, dict) else None},
            ) from exc
        instances.append({"instance_id": inst.instance_id, "type": inst.type, "params": inst.params})
    if not instances:
        raise bad_request("no_indicators", "at least one indicator is required")
    from backend.indicators.cache import get_or_compute, make_key, thaw_series
    from backend.indicators.compute import build_indicators

    data_version = run_data_version()
    # The cache key must identify this instrument's bar file, not just the
    # semantic alias: two instruments share "1m" and the tape version.
    key_data_version = (
        str(page.get("data_version") or data_version) if isinstance(page, dict) else data_version
    )
    key_bar_type = str(page.get("bar_type") or alias) if isinstance(page, dict) else alias
    series = thaw_series(
        get_or_compute(
            make_key(key_data_version, key_bar_type, instances),
            lambda: compute_series(rows, instances),
        )
    )
    if start_ns is not None or end_ns is not None:
        keep = [
            index
            for index, row in enumerate(rows)
            if (start_ns is None or int(row.get("ts_event_ns") or 0) >= start_ns)
            and (end_ns is None or int(row.get("ts_event_ns") or 0) < end_ns)
        ]
        rows = [rows[index] for index in keep]
        series = {
            instance_id: {
                output: [values[index] for index in keep] for output, values in by_output.items()
            }
            for instance_id, by_output in series.items()
        }
    warmup = build_indicators(instances).warmup()
    timestamps = [_iso(r.get("ts_event_ns") or _ns(r.get("ts_event"))) for r in rows]
    return {
        "instrument_id": instrument_id,
        "bar_type": alias,
        "data_version": key_data_version or data_version,
        "bars": len(rows),
        "series": series,
        "warmup": warmup,
        "ts_events": timestamps,
        "ts_event": timestamps,
        "timestamps": timestamps,
    }


# ------------------------------------------------------------------ quality/meta


def quality(
    instrument_id: str, start: str | None, end: str | None, warmup_bars: int | None = None
) -> dict[str, Any]:
    from backend.data import quality as q

    inst = get_instrument(instrument_id)
    fn = getattr(q, "quality", None)
    if fn is None:
        return {"instrument_id": instrument_id, "not_available": "quality module not available"}
    try:
        out = fn(
            instrument_id,
            _parse_date(start),
            _parse_date(end, end=True),
            warmup_bars=warmup_bars,
        )
    except TypeError:
        try:
            out = fn(instrument_id, _parse_date(start), _parse_date(end, end=True))
        except TypeError:
            out = fn(instrument_id)
    if not isinstance(out, dict):
        return {"instrument_id": instrument_id, "quality": out}
    out.setdefault("available_from", out.get("available_from_utc"))
    out.setdefault("available_to", out.get("available_to_utc"))
    out.setdefault("active_minute_pct", out.get("active_minutes_pct"))
    out.setdefault("longest_gap_s", (out.get("longest_gap_minutes") or 0) * 60 if out.get("longest_gap_minutes") is not None else None)
    out.setdefault("median_trade_usd", out.get("median_trade_size_usd"))
    out.setdefault("from", out.get("available_from_utc"))
    out.setdefault("to", out.get("available_to_utc"))
    return out


def lifecycle(instrument_id: str) -> dict[str, Any]:
    inst = get_instrument(instrument_id)
    from backend.data import events
    from backend.data import lifecycle as lc

    match_key = inst.get("match_key")
    observed = events.lifecycle_events(match_key) if match_key else []
    state = events.match_state(match_key)
    getter = getattr(lc, "lifecycle_for", None)
    if getter is None:
        return {"phase_source": "unavailable", "events": observed}
    row = getter(match_key)
    if row is None:
        return {
            "match_key": match_key,
            "phase": "unknown",
            "reason": "no verified lifecycle row",
            "events": observed,
            "match_state": state,
        }
    payload = row.as_dict() if hasattr(row, "as_dict") else dict(row)
    payload["events"] = observed
    payload["match_state"] = state
    return payload


def settlement_for(instrument_id: str) -> dict[str, Any] | None:
    inst = get_instrument(instrument_id)
    from backend.data import settlement

    getter = getattr(settlement, "get_settlement", None)
    if getter is None:
        return None
    row = getter(inst.get("condition_id"))
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return row.as_dict() if hasattr(row, "as_dict") else {k: getattr(row, k) for k in dir(row) if not k.startswith("_")}


class _SizingInstrument:
    """Duck-typed stand-in for ``position_sizing`` when a token has no expiry.

    ``position_sizing`` reads only ``size_increment``, ``size_precision`` and
    ``make_qty`` from its instrument (BRIEF §7), so a full Nautilus
    ``BinaryOption`` -- which requires a real expiry -- is not needed to show a
    pre-run size. Polymarket contracts trade in 0.01 steps at precision 2, and
    no expiry date is invented.
    """

    size_increment = 0.01
    size_precision = 2

    def make_qty(self, value: float, round_down: bool = True) -> float:
        return float(value)


def _observed_end_utc(match_key: Any) -> Any:
    """The lifecycle's observed match end, or ``None``; never built on demand."""
    if not match_key:
        return None
    try:
        from backend import config
        from backend.data.lifecycle import lifecycle_for

        path = config.LIFECYCLE_PATH
        if not path.exists():
            return None
        row = lifecycle_for(str(match_key), path)
    except Exception:  # noqa: BLE001 - advisory metadata, not a hard error
        return None
    return getattr(row, "actual_end_utc", None) if row is not None else None


def _sizing_instrument(row: dict[str, Any]) -> Any:
    """The instrument ``position_sizing`` needs, without inventing an expiry.

    Uses the row's own expiry; otherwise the observed lifecycle end (the same
    real expiry the runner honours); otherwise the no-expiry stand-in above.
    Trade timestamps are never used as an expiry.
    """
    from backend.engine.instruments import build_binary_option

    option_row = dict(row)
    option_id = str(option_row.get("instrument_id") or "")
    option_row.setdefault(
        "outcome", "NO" if option_id.endswith("-NO.POLYMARKET") else "YES"
    )
    option_row.setdefault("raw_symbol", option_row.get("token_symbol") or option_id)
    expiry_keys = ("expiration_ns", "end_date_utc", "end_date")
    if not any(option_row.get(key) for key in expiry_keys):
        observed = _observed_end_utc(option_row.get("match_key"))
        if observed is not None:
            option_row["end_date_utc"] = observed
    if any(option_row.get(key) for key in expiry_keys):
        return build_binary_option(option_row)
    return _SizingInstrument()


#: Minutes covered by one bar of each supported alias.  The quality report
#: counts active *minutes*, so a warm-up measured in bars must be scaled by
#: the alias before it is compared with that count.
_BAR_TYPE_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}


def _bar_type_minutes(alias: str | None) -> int:
    return _BAR_TYPE_MINUTES.get(alias or "1m", 1)


def _indicators_warmup(indicators: Any) -> int | None:
    """Largest declared warm-up among the request's indicator instances."""
    if not indicators:
        return None
    try:
        from backend.indicators.compute import build_indicators

        values = [
            int(value)
            for value in build_indicators(indicators).warmup().values()
            if value is not None
        ]
    except Exception:  # noqa: BLE001 - advisory only; rules validate on submit
        return None
    return max(values) if values else None


def preflight(body: dict[str, Any]) -> dict[str, Any]:
    from backend.engine.execution import calculate_cost_estimate, resolve_profile
    from backend.rules.schema import ExecutionConfig, RiskConfig

    cfg = ExecutionConfig.model_validate(body.get("execution", {}))
    risk_cfg = RiskConfig.model_validate(body.get("risk") or {})
    instrument_id = body.get("instrument_id")
    if not instrument_id:
        raise bad_request("missing_instrument", "instrument_id is required")
    rows = bars(instrument_id, body.get("bar_type", "1m"), body.get("from"), body.get("to"), 5000)["bars"]
    from backend.data.books import book_coverage

    from backend.data.instruments import get_instrument

    row = get_instrument(instrument_id)
    coverage = book_coverage(instrument_id, row.get("match_key") if row else None)
    # Expose the window as ISO dates at the top level: the UI and the
    # documented CoverageWindow shape read `from`/`to`, not raw nanoseconds.
    window = coverage.get("window") or {}
    if window.get("start_ns") is not None:
        coverage["from"] = _iso(window["start_ns"])
    if window.get("end_ns") is not None:
        coverage["to"] = _iso(window["end_ns"])
    coverage["snapshots"] = window.get("snapshots")
    try:
        effective = resolve_profile(
            cfg, has_book_coverage=bool(coverage.get("has_coverage")), coverage_window=None
        )
    except ValueError as exc:
        raise bad_request(
            "invalid_execution",
            str(exc),
            {"path": "execution", "profile": getattr(cfg, "profile", None)},
        ) from exc
    ref = None
    for r in rows:
        if r.get("close") is not None:
            ref = float(r["close"])
    estimate = None
    if ref is not None and 0.0 < ref < 1.0:
        try:
            estimate = calculate_cost_estimate(ref, effective, 1.0)
        except ValueError as exc:
            raise bad_request(
                "bad_cost_estimate", str(exc), {"path": "execution", "reference_price": ref}
            ) from exc
    if estimate is not None:
        if estimate.get("slippage_bps") is not None:
            estimate["per_side_bps"] = round(float(estimate.get("fee_bps") or 0) + float(estimate["slippage_bps"]), 4)
            estimate["per_side_price_points"] = estimate.get("slip_price_points")
        estimate.setdefault("note", "Per-side estimate; not a profitability claim.")
    sizing = None
    if row is not None and ref is not None and 0.0 < ref < 1.0:
        # Pre-run size and exposure, from the same sizing function the engine
        # uses; stake/contracts/max loss/max payout/max profit (BRIEF §7).
        from backend.engine.rule_strategy import position_sizing

        try:
            sizing = position_sizing(risk_cfg.sizing, ref, _sizing_instrument(dict(row)))
            sizing["reference_price"] = ref
        except (KeyError, TypeError, ValueError):
            # No expiry or malformed row: report no size rather than a guess.
            sizing = None
    report = None
    warnings: list[Any] = []
    if body.get("include_quality", True):
        warmup_bars = body.get("warmup_bars")
        if warmup_bars is not None:
            try:
                warmup_bars = int(warmup_bars)
            except (TypeError, ValueError):
                warmup_bars = None
        else:
            warmup_bars = _indicators_warmup(body.get("indicators"))
        if warmup_bars is not None:
            # ``quality`` compares warm-up against active 1-minute bars, so a
            # warm-up expressed in bars of the requested timeframe covers
            # ``multiplier`` times as many minutes.
            warmup_bars = warmup_bars * _bar_type_minutes(body.get("bar_type", "1m"))
        try:
            report = quality(
                instrument_id, body.get("from"), body.get("to"), warmup_bars=warmup_bars
            )
            warnings.extend(report.get("warnings") or [])
        except Exception:  # noqa: BLE001
            report = None
    if effective.fallback_reason:
        warnings.append(
            {
                "code": "book_fallback",
                "message": f"Book requested but this run will be priced as {effective.effective} ({effective.fallback_reason}).",
            }
        )
    return {
        "instrument_id": instrument_id,
        "quality": report,
        "warnings": warnings,
        "requested": effective.requested,
        "effective": effective.effective,
        "cost_source": effective.cost_source,
        "fallback_reason": effective.fallback_reason,
        "book_coverage": coverage,
        "cost_estimate": estimate,
        "sizing": sizing,
    }


def portfolio_optimize(body: dict[str, Any]) -> dict[str, Any]:
    """Compose 2-10 completed runs into one weighted portfolio.

    Only stored artifacts are read: every weight, Kelly fraction and equity
    point comes from a run's ``equity.parquet`` and ``summary.json``.  A run
    whose holdout split has not been revealed is restricted to its in-sample
    window so out-of-sample results cannot leak into the optimisation.
    """
    from backend.engine.portfolio import optimize_runs

    run_ids = list(
        dict.fromkeys(
            run_id
            for run_id in (body.get("run_ids") or [])
            if isinstance(run_id, str) and run_id.strip()
        )
    )
    if not 2 <= len(run_ids) <= 10:
        raise bad_request(
            "bad_portfolio",
            "portfolio optimisation requires between 2 and 10 distinct run ids",
            {"count": len(run_ids)},
        )

    sleeves: list[dict[str, Any]] = []
    extra_warnings: list[str] = []
    for run_id in run_ids:
        run = state.get_run(run_id)
        if run is None:
            raise not_found("run", run_id)
        if run.get("status") != "complete":
            status = run.get("status")
            raise bad_request(
                "run_not_complete",
                f"run '{run_id}' has status '{status}', "
                "only completed runs can be optimised",
                {"run_id": run_id, "status": status},
            )
        equity = parquet_rows(run_id, "equity.parquet", 200_000)
        summary = read_json_artifact(run_id, "summary.json") or {}
        config = run.get("config") or {}
        split = (config.get("evaluation") or {}).get("split")
        # A completed run with an unrevealed split must contribute only its
        # in-sample history; the holdout stays behind the explicit reveal.
        unrevealed = isinstance(split, dict) and bool(split) and (
            int(run.get("holdout_reveals") or 0) <= 0
        )
        if unrevealed:
            train_end = split.get("train_end")
            train_end_ns = _parse_date(train_end, end=True) if train_end else None
            if train_end_ns is not None:
                equity = [
                    point
                    for point in equity
                    if (point.get("ts_ns") or _ns(point.get("ts")) or 0) < train_end_ns
                ]
            summary = summary.get("in_sample") or {}
            extra_warnings.append(
                f"Run {run_id[:8]} has an unrevealed holdout split; "
                f"restricted to in-sample window through {train_end}."
            )
        strategy_cfg = config.get("strategy") or {}
        sleeves.append(
            {
                "run_id": run_id,
                "instrument_id": strategy_cfg.get("instrument_id"),
                "strategy_hash": run.get("strategy_hash"),
                "requested_profile": run.get("requested_profile"),
                "effective_profile": run.get("effective_profile"),
                "equity": equity,
                "metrics": summary,
            }
        )

    try:
        result = optimize_runs(
            sleeves,
            method=body.get("method") or "three_stage",
            max_weight=float(body.get("max_weight", 0.35)),
            vol_window=int(body.get("vol_window", 20)),
            min_periods=int(body.get("min_periods", 5)),
            correlation_penalty=float(body.get("correlation_penalty", 0.5)),
            max_pair_correlation=float(body.get("max_pair_correlation", 0.95)),
            kelly_fraction=float(body.get("kelly_fraction", 0.25)),
            lcb_z=float(body.get("lcb_z", 1.0)),
            bankroll_usd=float(body.get("bankroll_usd", 1000.0)),
        )
    except ValueError as exc:
        raise bad_request("bad_portfolio", str(exc)) from exc

    result["warnings"] = extra_warnings + list(result.get("warnings") or [])
    return result
