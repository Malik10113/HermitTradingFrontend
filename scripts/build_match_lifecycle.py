#!/usr/bin/env python
"""Build ``var/lifecycle/match_lifecycle.parquet`` from cached ESPN summaries.

The default run is fully offline: fixtures come from
``data/events/fixture_map.parquet`` and the summaries from
``data/events/espn/summary``.  ``--no-backfill`` documents that no network
access will be attempted; v1 never calls the ESPN API from this command.

Rows missing either observed wallclock are omitted, which downstream reads as
``phase = unknown``.  A nominal match duration is never substituted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ensure_dirs  # noqa: E402
from backend.data.lifecycle import build_match_lifecycle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="cap fixtures processed")
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="explicitly offline (the default); accepted for compatibility",
    )
    parser.add_argument("--out", type=Path, default=None, help="override output path")
    args = parser.parse_args(argv)

    ensure_dirs()
    result = build_match_lifecycle(
        limit=args.limit,
        backfill=not args.no_backfill,
        out_path=args.out,
    )
    print(f"path={result['path']}")
    print(
        f"fixtures={result['fixtures']} rows={result['rows']} "
        f"missing_summary={result['missing_summary']} "
        f"incomplete={result['incomplete']} invalid={result['invalid']}"
    )
    print(f"lifecycle_version={result['lifecycle_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
