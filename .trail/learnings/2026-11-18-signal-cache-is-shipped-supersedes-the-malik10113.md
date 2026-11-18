---
title: "Signal cache is shipped: supersedes the no-cache v1 boundary"
author: Malik10113
date: 2026-11-18
branch: feature/signal-cache
id: l-021744b5
scope: repo
status: active
replaces: l-3ae2af82
src: agent
touches:
  - backend/engine/signals.py
  - docs/IMPLEMENTATION.md
---

## Decided
- The v1 boundary note said there is no signal cache and docs/IMPLEMENTATION.md should say the same. That is no longer true: backend/engine/signals.py now ships a two-tier signal plan cache (process LRU + var/cache/signals/) and the runner resolves a plan before every run, including in worker subprocesses.
- Keep the other two parts of the v1 boundary note: SweepsPanel is still one-axis, and Book still uses the latest L2 snapshot at or before the decision.

## Watch out
Any doc or review that repeats "there is no signal cache in v1" is stale; use the Caching section of docs/IMPLEMENTATION.md instead.
