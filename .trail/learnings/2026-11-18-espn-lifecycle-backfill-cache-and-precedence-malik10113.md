---
title: ESPN lifecycle backfill cache and precedence
author: Malik10113
date: 2026-11-18
branch: feature/lifecycle-backfill
id: l-ec75f0d0
scope: repo
status: active
src: agent
touches:
  - backend/data/lifecycle.py
  - backend/data/lifecycle_backfill.py
  - scripts/build_match_lifecycle.py
---

## Decided
- The 2026 ESPN summary cache keeps its Polymarket league filenames (`epl_111.json.gz`), but the new backfill cache uses ESPN competition slugs under `var/raw/espn/summary/{eng.1|lal...}_{event_id}.json.gz` and `var/raw/espn/scoreboard/{slug}_{YYYY-MM-DD}.json.gz`.
- Backfill matching is exact-normalised on date plus team names/ESPN abbreviations; no fuzzy match. Zero or multiple candidates count as `unmatched`.
- Precedence is cached summary > existing lifecycle artifact row > newly backfilled row. `--refresh` re-fetches caches but still never overwrites an existing observed row.
- Rows missing either `kickoff` or `end-regular-time` wallclock are omitted and counted `incomplete`; no nominal duration.
## Why
- Rewriting the 2026 source cache is forbidden, and the requested var cache path is keyed by ESPN slug. Mixing the two names would make cached lookups miss.
- Preserving artifact rows makes reruns idempotent even if an ESPN payload later changes; the content token for those rows is folded into `lifecycle_version`.
## Watch out
- `--limit` still caps the offline fixture scan as before, so a backfill run can write cached rows plus N backfilled rows.
- Source-cache lookup must use the Polymarket league code; var-cache lookup uses the ESPN slug.
