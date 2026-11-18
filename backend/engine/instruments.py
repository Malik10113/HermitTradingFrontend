"""Nautilus ``BinaryOption`` construction from instrument-index rows.

One instrument per outcome token (BRIEF §5):

    epl-bou-ars-2024-10-19-bou-YES.POLYMARKET   # primary token
    epl-bou-ars-2024-10-19-bou-NO.POLYMARKET    # complement token

The index row is the only source of instrument metadata.  Nothing is invented:
a missing symbol, slug, outcome or expiration is an error naming the field, and
price/settlement values are validated against the 0-1 probability domain.

Polymarket prices carry the venue's recorded precision; the fallback
``0.001`` is used only when the row omits ``price_increment``, and it is then
recorded as the venue tick.  ``size_precision`` is 2 with a ``0.01`` contract
increment so every quantity can be built with ``instrument.make_qty`` (rc9
silently drops quantities whose precision differs from the instrument).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping

from nautilus_trader.model import (
    AssetClass,
    BinaryOption,
    Currency,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
)

VENUE = "POLYMARKET"
CURRENCY = "USD"
DEFAULT_PRICE_INCREMENT = "0.001"
DEFAULT_PRICE_PRECISION = 3
SIZE_PRECISION = 2
SIZE_INCREMENT = "0.01"
MIN_PRICE = "0.001"
MAX_PRICE = "0.999"
OUTCOMES = ("YES", "NO")

# Keys a caller may use for the two tokens of a condition.  ``token1`` is YES
# and ``token2`` is NO in ``polymarket/sii/markets.parquet``.
_YES_KEYS = ("token_yes", "token1", "yes_token")
_NO_KEYS = ("token_no", "token2", "no_token")
_SYMBOL_KEYS = ("symbol", "raw_symbol", "outcome_symbol")
_EXPIRY_KEYS = ("expiration_ns", "end_date_utc", "end_date")


# ---------------------------------------------------------------------------
# Validation helpers


def _require(row: Mapping[str, Any], keys: tuple[str, ...], what: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    raise ValueError(f"instrument row is missing {what} (looked for {', '.join(keys)})")


def validate_price(value: float, *, field: str = "price") -> float:
    """A probability in ``[0, 1]``; anything else is a data error."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number, got {value!r}") from exc
    if number != number or not (0.0 <= number <= 1.0):  # NaN-safe
        raise ValueError(f"{field} must be in [0, 1], got {value!r}")
    return number


def validate_settlement_value(value: Any, *, field: str = "settlement_value") -> int:
    """Settlement is exactly 0 or 1; never inferred from a price."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be 0 or 1, got {value!r}") from exc
    if number == 0.0:
        return 0
    if number == 1.0:
        return 1
    raise ValueError(f"{field} must be 0 or 1, got {value!r}")


def _epoch_ns(value: Any, *, field: str) -> int:
    """Convert an ISO-8601 UTC value or a nanosecond integer to ns since epoch.

    Naive datetimes are rejected (the app is UTC-only).  A date-only value is
    the start of that UTC day; it is the metadata cutoff, not a verified
    settlement timestamp.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp or ns integer")
    if isinstance(value, (int,)):
        if value <= 0:
            raise ValueError(f"{field} must be a positive epoch-nanosecond value")
        return int(value)
    if isinstance(value, float):
        if value <= 0:
            raise ValueError(f"{field} must be a positive epoch-nanosecond value")
        return int(value)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min, tzinfo=timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"{field} is empty")
        if raw.isdigit():
            return _epoch_ns(int(raw), field=field)
        text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} is not ISO-8601: {value!r}") from exc
    else:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp or ns integer")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{field} must be timezone-aware UTC, got naive {value!r}")
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1_000


def _price(value: Any, *, field: str, default: str | None = None) -> Price:
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{field} is required")
        value = default
    try:
        price = Price.from_str(str(value))
    except Exception as exc:  # noqa: BLE001 - Cython error types vary
        raise ValueError(f"{field} is not a valid price: {value!r}") from exc
    if float(price) < 0.0:
        raise ValueError(f"{field} must be non-negative, got {value!r}")
    return price


def _int(value: Any, *, field: str, default: int | None = None) -> int:
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{field} is required")
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer, got {value!r}") from exc
    if number != float(value):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    return number


# ---------------------------------------------------------------------------
# Identifiers


def instrument_id_for(slug: str, token: str) -> str:
    """``{slug}-{YES|NO}.POLYMARKET``; validated by Nautilus' parser."""
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug is required to build an instrument id")
    token_upper = str(token).upper()
    if token_upper not in OUTCOMES:
        raise ValueError(f"token must be YES or NO, got {token!r}")
    value = f"{slug.strip()}-{token_upper}.{VENUE}"
    try:
        InstrumentId.from_str(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid instrument id {value!r}: {exc}") from exc
    return value


def pair_ids_for(instrument_id: str) -> tuple[str, str]:
    """Return ``(yes_id, no_id)`` for either token of the same condition."""
    if not isinstance(instrument_id, str) or not instrument_id:
        raise ValueError("instrument_id is required")
    symbol, _, venue = instrument_id.rpartition(".")
    for token in OUTCOMES:
        suffix = f"-{token}"
        if symbol.endswith(suffix):
            base = symbol[: -len(suffix)]
            if not base:
                raise ValueError(f"cannot derive the sibling id of {instrument_id!r}")
            return (
                f"{base}-YES.{venue or VENUE}",
                f"{base}-NO.{venue or VENUE}",
            )
    raise ValueError(
        f"{instrument_id!r} does not end in -YES or -NO; not a binary-option pair"
    )


def pair_slug(instrument_id: str) -> str:
    """The condition slug shared by a token pair."""
    symbol, _, _ = instrument_id.rpartition(".")
    for token in OUTCOMES:
        suffix = f"-{token}"
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    raise ValueError(f"{instrument_id!r} does not end in -YES or -NO")


# ---------------------------------------------------------------------------
# Instruments


def build_binary_option(row: Mapping[str, Any]) -> BinaryOption:
    """Build one ``BinaryOption`` from an instrument-index row.

    Required: ``instrument_id``, raw symbol, outcome (YES/NO) and an expiry
    (``expiration_ns`` or ``end_date_utc``).  Optional: ``question`` /
    ``description``, ``price_precision`` (default 3), ``price_increment``
    (default ``0.001``), ``size_precision`` (default 2) and ``size_increment``
    (default ``0.01``).
    """
    if not isinstance(row, Mapping):
        raise ValueError("instrument row must be a mapping")

    instrument_id = _require(row, ("instrument_id",), "instrument_id")
    try:
        InstrumentId.from_str(str(instrument_id))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid instrument_id {instrument_id!r}: {exc}") from exc

    raw_symbol = _require(row, _SYMBOL_KEYS, "raw symbol")
    try:
        symbol = Symbol(str(raw_symbol))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid raw symbol {raw_symbol!r}: {exc}") from exc

    outcome = str(_require(row, ("outcome",), "outcome")).upper()
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be YES or NO, got {row.get('outcome')!r}")

    expiry_raw = _require(row, _EXPIRY_KEYS, "expiration (expiration_ns or end_date_utc)")
    expiration_ns = _epoch_ns(expiry_raw, field="expiration_ns")
    if expiration_ns <= 0:
        raise ValueError("expiration_ns must be positive")

    price_precision = _int(
        row.get("price_precision"), field="price_precision", default=DEFAULT_PRICE_PRECISION
    )
    if not 0 <= price_precision <= 10:
        raise ValueError(f"price_precision must be in [0, 10], got {price_precision}")
    price_increment = _price(
        row.get("price_increment"),
        field="price_increment",
        default=DEFAULT_PRICE_INCREMENT,
    )
    if price_increment.precision != price_precision:
        raise ValueError(
            f"price_increment {price_increment} has precision "
            f"{price_increment.precision}, but price_precision is {price_precision}"
        )

    size_precision = _int(
        row.get("size_precision"), field="size_precision", default=SIZE_PRECISION
    )
    if not 0 <= size_precision <= 10:
        raise ValueError(f"size_precision must be in [0, 10], got {size_precision}")
    size_increment = Quantity.from_str(
        str(row.get("size_increment") or SIZE_INCREMENT)
    )
    if size_increment.precision != size_precision:
        raise ValueError(
            f"size_increment {size_increment} has precision "
            f"{size_increment.precision}, but size_precision is {size_precision}"
        )

    min_price = _price(row.get("min_price"), field="min_price", default=MIN_PRICE)
    max_price = _price(row.get("max_price"), field="max_price", default=MAX_PRICE)
    if not (0.0 <= float(min_price) < float(max_price) <= 1.0):
        raise ValueError(
            f"expected 0 <= min_price {min_price} < max_price {max_price} <= 1"
        )

    question = row.get("question") or row.get("description")

    return BinaryOption(
        instrument_id=InstrumentId.from_str(str(instrument_id)),
        raw_symbol=symbol,
        asset_class=AssetClass.ALTERNATIVE,
        currency=Currency.from_str(CURRENCY),
        activation_ns=0,
        expiration_ns=expiration_ns,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=price_increment,
        size_increment=size_increment,
        ts_event=0,
        ts_init=0,
        outcome=outcome,
        description=str(question) if question else None,
        max_price=max_price,
        min_price=min_price,
    )


def build_pair(row: Mapping[str, Any]) -> tuple[BinaryOption, BinaryOption]:
    """Build ``(primary_yes, complement_no)`` for one market condition.

    The row needs the condition ``slug`` (or ``instrument_id`` of a token),
    the two raw token symbols, a YES/NO outcome label per token (optional,
    derived from ``answer1``/``answer2`` when absent) and one expiry.
    """
    if not isinstance(row, Mapping):
        raise ValueError("instrument row must be a mapping")

    if row.get("slug"):
        slug = str(row["slug"])
    elif row.get("instrument_id"):
        slug = pair_slug(str(row["instrument_id"]))
    else:
        raise ValueError("instrument row is missing slug")

    yes_token = _require(row, _YES_KEYS, "YES token symbol")
    no_token = _require(row, _NO_KEYS, "NO token symbol")

    base = dict(row)
    base.pop("instrument_id", None)
    base.pop("outcome", None)

    yes_row = dict(base)
    yes_row.update(
        instrument_id=instrument_id_for(slug, "YES"),
        symbol=str(yes_token),
        outcome="YES",
    )
    no_row = dict(base)
    no_row.update(
        instrument_id=instrument_id_for(slug, "NO"),
        symbol=str(no_token),
        outcome="NO",
    )
    return build_binary_option(yes_row), build_binary_option(no_row)


def describe_instrument(instrument: BinaryOption) -> dict[str, Any]:
    """Plain-JSON metadata for the API; values come from the instrument."""
    return {
        "instrument_id": str(instrument.id),
        "raw_symbol": str(instrument.raw_symbol),
        "venue": str(instrument.venue),
        "outcome": instrument.outcome,
        "description": instrument.description,
        "asset_class": str(instrument.asset_class),
        "currency": str(instrument.currency),
        "activation_ns": instrument.activation_ns,
        "expiration_ns": instrument.expiration_ns,
        "price_precision": instrument.price_precision,
        "price_increment": str(instrument.price_increment),
        "size_precision": instrument.size_precision,
        "size_increment": str(instrument.size_increment),
        "min_price": str(instrument.min_price) if instrument.min_price is not None else None,
        "max_price": str(instrument.max_price) if instrument.max_price is not None else None,
        "ts_event": instrument.ts_event,
        "ts_init": instrument.ts_init,
    }


__all__ = [
    "build_binary_option",
    "build_pair",
    "describe_instrument",
    "instrument_id_for",
    "pair_ids_for",
    "pair_slug",
    "validate_price",
    "validate_settlement_value",
]
