#!/usr/bin/env python
"""Write a Nautilus ``ParquetDataCatalog`` for cached instrument bars.

The catalog lives under ``var/catalog``.  It is rebuilt into a temporary
directory and atomically swapped, so re-running is idempotent (no duplicate
partitions).  Quantities are built with ``instrument.make_qty`` so the matching
engine accepts them (see the rc9 learning).

Examples::

    uv run python scripts/build_catalog.py --instrument epl-bou-ars-2024-10-19-bou-YES.POLYMARKET
    uv run python scripts/build_catalog.py --all --limit 10 --alias 1m --alias 5m

This operational script is the only place outside ``backend/engine`` that builds
Nautilus instrument objects; strategy/execution code keeps using the engine
adapter.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nautilus_trader.model import (  # noqa: E402
    AssetClass,
    Bar,
    BinaryOption,
    Currency,
    InstrumentId,
    Price,
    Quantity,
)
from nautilus_trader.persistence import ParquetDataCatalog  # noqa: E402

from backend import config  # noqa: E402
from backend.data import bars, instruments, versions  # noqa: E402
from backend.data.timeutil import to_ns  # noqa: E402

SIZE_PRECISION = 2
SIZE_INCREMENT = "0.01"


def _price_increment(precision: int) -> Price:
    return Price.from_str(f"0.{'0' * (precision - 1)}1")


def _instrument(row: dict) -> BinaryOption:
    instrument_id = InstrumentId.from_str(row["instrument_id"])
    precision = int(row["price_precision"])
    expiration_ns = to_ns(row["end_date_utc"]) if row.get("end_date_utc") else 0
    return BinaryOption(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        asset_class=AssetClass.ALTERNATIVE,
        currency=Currency.from_str("USD"),
        activation_ns=0,
        expiration_ns=expiration_ns,
        price_precision=precision,
        size_precision=SIZE_PRECISION,
        price_increment=_price_increment(precision),
        size_increment=Quantity.from_str(SIZE_INCREMENT),
        ts_event=0,
        ts_init=0,
        outcome="YES" if int(row["token_index"]) == 0 else "NO",
        description=row.get("question"),
    )


def _bar(instrument: BinaryOption, bar_type, row: dict) -> Bar:
    return Bar(
        bar_type=bar_type,
        open=instrument.make_price(row["open"]),
        high=instrument.make_price(row["high"]),
        low=instrument.make_price(row["low"]),
        close=instrument.make_price(row["close"]),
        volume=instrument.make_qty(row["volume"]),
        ts_event=int(row["ts_event"]),
        ts_init=int(row["ts_init"]),
    )


def _aliases(values: list[str] | None) -> list[str]:
    if not values:
        return [bars.DEFAULT_ALIAS]
    out: list[str] = []
    for value in values:
        if value.lower() == "all":
            out.extend([*bars.TIME_ALIAS_ORDER, *bars.NON_TIME_ALIASES])
        else:
            out.append(value)
    return list(dict.fromkeys(out))


def _select_instruments(args: argparse.Namespace) -> list[dict]:
    rows = instruments.load_index()
    if args.instrument:
        selected = []
        for instrument_id in args.instrument:
            row = next((item for item in rows if item["instrument_id"] == instrument_id), None)
            if row is None:
                raise SystemExit(f"unknown instrument: {instrument_id}")
            selected.append(row)
        return selected
    if args.all:
        if args.limit is not None:
            if args.limit < 0:
                raise SystemExit("--limit must be >= 0")
            rows = rows[: args.limit]
        return rows
    raise SystemExit("choose --instrument <id> (repeatable) or --all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--alias", action="append", default=[])
    args = parser.parse_args(argv)

    config.ensure_dirs()
    selected = _select_instruments(args)
    aliases = _aliases(args.alias)
    target = Path(config.CATALOG_DIR)
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)

    written_bars = 0
    try:
        catalog = ParquetDataCatalog(str(temp))
        catalog.write_instruments([_instrument(row) for row in selected])
        for row in selected:
            instrument = _instrument(row)
            for alias in aliases:
                info = bars.ensure_bars_file(row["instrument_id"], alias)
                rows = bars.build_bars(row["instrument_id"], info["alias"])
                if not rows:
                    continue
                bar_type = bars.bar_type_object(row["instrument_id"], info["alias"])
                catalog.write_bars([_bar(instrument, bar_type, item) for item in rows])
                written_bars += len(rows)
                print(
                    f"{row['instrument_id']} {info['alias']} bars={len(rows)}",
                    flush=True,
                )
        if target.exists():
            shutil.rmtree(target)
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    print(f"catalog={target} instruments={len(selected)} bars={written_bars}")
    print(f"data_version={versions.tape_data_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
