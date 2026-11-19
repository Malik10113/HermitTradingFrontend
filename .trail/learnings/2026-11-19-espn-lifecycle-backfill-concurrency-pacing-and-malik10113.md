---
title: "ESPN lifecycle backfill concurrency, pacing and staging"
author: Malik10113
date: 2026-11-19
branch: feature/lifecycle-backfill
id: l-15129476
scope: repo
status: active
src: agent
touches:
  - backend/data/lifecycle.py
  - backend/data/lifecycle_backfill.py
  - scripts/build_match_lifecycle.py
  - tests/data/test_lifecycle_backfill.py
---

## Decided
- `--concurrency` defaults to 1 and is capped at 8. It parallelises summary fetches inside each league/date group only; scoreboard dates stay serial and the lifecycle parquet is written once by the main thread.
- The rate limiter reserves request start slots under one shared lock, so the 0.25 s default delay is global, not multiplied by workers. Retries/backoff remain inside `fetch_json`.
- Duplicate summary URLs are coalesced with an in-run singleflight keyed by ESPN league + event id; duplicate scoreboard URLs cannot occur because fixtures are grouped by league/date.
- Cache writes hold one process-wide lock, use a pid+thread temp name, and pin the gzip header and filesystem mtime to 0 so identical payloads produce the same data-version fingerprint.
- `--stage` writes `<out>.next` and `os.replace`s the real lifecycle only after the whole run succeeds.
## Why
- The remaining Big-4 backfill is network/pacing bound and should be safe to run in the background while the frontend is deployed.
- Pinning cache mtimes makes serial and concurrent runs produce the same `lifecycle_version`; the version is a function of content, not when a worker happened to write.
## Watch out
- Global pacing means concurrency only recovers request latency; it does not increase request rate. On the EPL 2024-11-01..30 slice, serial was 26.69 s and concurrency 4 was 24.33 s at 46 requests.
- Scoreboard dates are the remaining serial bottleneck. Parallelising date groups would need the same global limiter and careful per-date error handling.
- Existing observed rows and cached summaries still win; `--refresh` refetches caches but does not overwrite those rows.
