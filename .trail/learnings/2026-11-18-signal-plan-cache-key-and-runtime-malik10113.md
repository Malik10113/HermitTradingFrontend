---
title: Signal plan cache key and runtime parity
author: Malik10113
date: 2026-11-18
branch: feature/signal-cache
id: l-8b3c4c29
scope: repo
status: active
src: agent
touches:
  - backend/engine/rule_strategy.py
  - backend/engine/runner.py
---

## Discovered
- A signal plan must be computed from `bar_to_dict(bar)` of the exact Nautilus `Bar` objects the engine will dispatch, not from the raw parquet rows: `instrument.make_price` rounds to the venue price precision, so feeding raw closes to indicators would let plan and runtime cross/compare on different values.
- `strategy_hash` hashes the whole strategy section, which already contains `instrument_id` and `bar_type`, so the brief's key `(data_version, bar_type, strategy_hash, warmup_bars, indicator_impl_version)` cannot collide across instruments. Do not add an instrument field to the key.
- `run_data_version()` fingerprints `LIFECYCLE_PATH` and `SETTLEMENT_PATH`, so lifecycle changes already change the data version. The plan stores `lifecycle_version` and treats a known mismatch as a cache miss; it is not a key component.
- Runtime and plan truth agree by construction only if the runtime path keeps its short-circuit order: pass a `truth(action)` callable to `_decide` (runtime: evaluate lazily; plan: read the row) instead of precomputing all four truths.
## Why
These decide whether criterion 19 (same signals under a new execution profile) is true or merely looks true, and they are invisible in the plan rows.
