---
title: v1 verification traps: empty parquet rows, aggregated bars, path-dependent versions
author: Malik10113
date: 2026-11-18
branch: verify-v1
id: l-7c1e4a92
scope: repo
status: active
src: agent
touches:
  - backend/engine/artifacts.py
  - backend/data/bars.py
  - backend/data/versions.py
---

## Discovered
- `pyarrow.Table.slice(0, limit)` on a **zero-column** table returns `limit` empty rows. An empty trades/signals artifact is written as `pa.table({})`, so reading it with slice fabricated 100,000 phantom trades. Guard `num_rows == 0 or num_columns == 0` before slicing.
- Nautilus rc9 `BacktestEngine.add_data` rejects a composite bar type: `bar_type.aggregation_source must be External, was Internal`. Stored 5m/15m/1h/1d files are replayed as `...-<step>-...-LAST-EXTERNAL` with `ts_init = ts_event + interval`; the authored alias stays in config/manifest. `ts_init = ts_event + 1` for an aggregated bar also prevents bar execution.
- A data version that hashes the absolute `var/` path is checkout-dependent: copying the same `var/` into a worktree changed every version and orphaned every run. Hash paths relative to `HTF_DATA_ROOT`, `var/` or the repo root, and bump a schema constant so the version cache is recalculated.
- FastAPI body validation returns `{"detail": [...]}`, not the documented `{code, message, details}`. Register a `RequestValidationError` handler; the frontend's `ApiError.fromResponse` only parses the stable shape.

## Why
Each of these silently corrupts a displayed number or breaks a supported bar type; they are not visible from unit tests that only use 1m bars and non-empty artifacts.
