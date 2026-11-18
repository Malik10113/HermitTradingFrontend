---
title: Persisting signal plans across worker processes
author: Malik10113
date: 2026-11-18
branch: feature/signal-cache
id: l-8c8857e4
scope: repo
status: active
src: agent
touches:
  - backend/engine/signals.py
  - backend/config.py
  - backend/engine/runner.py
---

## Discovered
- The signal cache had to become two-tier because each API run is a separate worker process; an in-process LRU can never satisfy criterion 19 in the real path.
- Write order matters: parquet first, metadata JSON last, both via temp-file + `os.replace`. The metadata is the commit marker, so a crash leaves an orphan parquet that is never served and is cleaned after an hour, not immediately (a concurrent writer may be between its parquet rename and its metadata commit).
- A lifecycle-version mismatch is a miss but must NOT delete the entry: the plan is still valid for the data version it was written under (and `run_data_version()` already covers the lifecycle file). Only malformed/partial/content-hash-mismatched files are deleted.
- Reconstructing the plan runs `SignalPlan.__post_init__`, which recomputes `plan_hash`; comparing it to the metadata is a free end-to-end integrity check over rows, key and lifecycle.
- Recency for LRU eviction is the metadata file's mtime, refreshed on every disk hit with `os.utime`; atime is unreliable under relatime.

## Why
These are the decisions that make the disk cache safe to serve from another process and keep corruption from silently changing backtest decisions.
