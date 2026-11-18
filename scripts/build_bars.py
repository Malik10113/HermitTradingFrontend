#!/usr/bin/env python
"""Build and cache Polymarket bars under ``var/bars``.

Examples::

    uv run python scripts/build_bars.py --instrument epl-bou-ars-2024-10-19-bou-YES.POLYMARKET
    uv run python scripts/build_bars.py --all --limit 20 --alias 1m --alias 5m
    uv run python scripts/build_bars.py --all --limit 5 --alias all

The command prints the data version once at the end.  It never writes outside
``var/`` and never edits the sibling data repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python scripts/build_bars.py`` without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ensure_dirs  # noqa: E402
from backend.data import bars, instruments, versions  # noqa: E402


def _aliases(values: list[str] | None) -> list[str]:
    if not values:
        return [bars.DEFAULT_ALIAS]
    out: list[str] = []
    for value in values:
        if value.lower() == "all":
            out.extend([*bars.TIME_ALIAS_ORDER, *bars.NON_TIME_ALIASES])
        else:
            out.append(value)
    # de-duplicate while preserving order
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
    parser.add_argument("--instrument", action="append", default=[], help="instrument id")
    parser.add_argument("--all", action="store_true", help="build every indexed instrument")
    parser.add_argument("--limit", type=int, default=None, help="cap --all to N instruments")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="bar alias (1m, 5m, 15m, 1h, 1d, tick_100, volume_1000, "
        "value_10000, renko_0.01, or 'all'); repeatable",
    )
    args = parser.parse_args(argv)

    ensure_dirs()
    selected = _select_instruments(args)
    aliases = _aliases(args.alias)
    data_version = versions.tape_data_version()
    total_rows = 0
    for index, row in enumerate(selected, start=1):
        for alias in aliases:
            info = bars.ensure_bars_file(row["instrument_id"], alias)
            total_rows += info["rows"]
            print(
                f"[{index}/{len(selected)}] {row['instrument_id']} "
                f"{info['alias']} rows={info['rows']} -> {info['path']}"
            )

    print(f"instruments={len(selected)} aliases={','.join(aliases)} rows={total_rows}")
    print(f"data_version={data_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
