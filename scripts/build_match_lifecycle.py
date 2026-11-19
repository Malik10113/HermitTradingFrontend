#!/usr/bin/env python
"""Build ``var/lifecycle/match_lifecycle.parquet`` from ESPN keyEvents.

The default run is fully offline: fixtures come from
``data/events/fixture_map.parquet`` and the summaries from
``data/events/espn/summary``.  ``--no-backfill`` documents that no network
access will be attempted.

``--backfill`` adds a resumable scoreboard pass for fixtures the 2026 cache does
not cover.  Responses are cached gzipped under ``var/raw/espn``; a second run
reuses them and makes no requests.  A cached observation is never overwritten
by a backfilled one.

``--resolve-index`` additionally takes fixtures from the generated instrument
index, so a match the results spine never mapped can still be resolved.

Rows missing either observed wallclock are omitted, which downstream reads as
``phase = unknown``.  A nominal match duration is never substituted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ensure_dirs  # noqa: E402
from backend.data.lifecycle import build_match_lifecycle  # noqa: E402
from backend.data.lifecycle_backfill import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    DEFAULT_DELAY_S,
    MAX_CONCURRENCY,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {value!r}"
        ) from exc


def _league_list(value: str) -> list[str]:
    leagues = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not leagues:
        raise argparse.ArgumentTypeError("expected a comma-separated league list")
    return leagues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="cap fixtures processed")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="fetch missing fixtures from the ESPN scoreboard (network)",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="explicitly offline (the default); accepted for compatibility",
    )
    parser.add_argument("--from", dest="from_date", type=_iso_date, default=None)
    parser.add_argument("--to", dest="to_date", type=_iso_date, default=None)
    parser.add_argument(
        "--leagues",
        type=_league_list,
        default=None,
        help="comma-separated Polymarket leagues (epl,lal,bun,fl1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_S,
        help=f"minimum seconds between network request starts (default {DEFAULT_DELAY_S})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=(
            "summary fetches per date group; 1 is serial "
            f"(default {DEFAULT_CONCURRENCY}, cap {MAX_CONCURRENCY})"
        ),
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="write <out>.next and replace the lifecycle only after success",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore cached responses and re-fetch; never required",
    )
    parser.add_argument(
        "--resolve-index",
        action="store_true",
        help=(
            "also resolve fixtures from the instrument index that the results "
            "spine never mapped (requires --backfill)"
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="override output path")
    args = parser.parse_args(argv)

    if args.backfill and args.no_backfill:
        parser.error("--backfill and --no-backfill are mutually exclusive")
    if not args.backfill and (
        args.from_date
        or args.to_date
        or args.leagues
        or args.refresh
        or args.resolve_index
        or args.concurrency != DEFAULT_CONCURRENCY
    ):
        parser.error(
            "--from/--to/--leagues/--refresh/--resolve-index/--concurrency require "
            "--backfill; the default path is offline"
        )
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")

    ensure_dirs()
    result = build_match_lifecycle(
        limit=args.limit,
        backfill=args.backfill,
        out_path=args.out,
        from_date=args.from_date,
        to_date=args.to_date,
        leagues=args.leagues,
        delay_s=args.delay,
        concurrency=args.concurrency,
        refresh=args.refresh,
        resolve_index=args.resolve_index,
        stage=args.stage,
    )
    print(f"path={result['path']}")
    print(
        f"fixtures={result['fixtures']} rows={result['rows']} "
        f"missing_summary={result['missing_summary']} "
        f"incomplete={result['incomplete']} invalid={result['invalid']} "
        f"preserved_existing={result['preserved_existing']}"
    )
    report = result.get("backfill_report")
    if report is not None:
        print(
            "backfill "
            f"dates_scanned={report['dates_scanned']} "
            f"requests={report['requests']} "
            f"cache_hits={report['cache_hits']} "
            f"matched={report['matched']} "
            f"unmatched={report['unmatched']} "
            f"incomplete={report['incomplete']} "
            f"skipped={report['skipped']} "
            f"rows_written={report['rows_written']} "
            f"fixtures={report['fixtures']} "
            f"concurrency={report['concurrency']} "
            f"coalesced={report['coalesced']}"
        )
        if report["errors"]:
            print(f"backfill_errors={len(report['errors'])}")
            for error in report["errors"]:
                print(f"  error: {error}")
        print("backfill_report=" + json.dumps(report, sort_keys=True))
    print(f"lifecycle_version={result['lifecycle_version']}")
    print(f"built_on={result['built_on']}")
    print(f"staged={str(result['staged']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
