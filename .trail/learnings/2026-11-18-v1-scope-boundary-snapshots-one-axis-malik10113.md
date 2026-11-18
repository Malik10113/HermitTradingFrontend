---
title: "v1 scope boundary: snapshots, one-axis sweep, no signal cache"
author: Malik10113
date: 2026-11-18
branch: docs/v1
id: l-3ae2af82
scope: repo
status: active
src: agent
touches:
  - docs/SESSION_MASTER_2026-11-18_V1.md
  - docs/IMPLEMENTATION.md
  - docs/BRIEF.md
  - backend/engine/runner.py
  - frontend/src/components/results/SweepsPanel.tsx
---

## Decided
- The v1 boundary is one Polymarket instrument pair per run, five execution profiles, and Book using the latest L2 snapshot at or before the decision. It does not replay ticks, does not model queue position, does not do multi-instrument portfolios, has no Kelly sizing and no range bars. Those stay v2/v1.1.
- Two brief-level promises are deliberately **not** v1 facts and docs must say so: (1) `SweepsPanel` exposes one axis and a progress table, while the API accepts two axes; there is no heatmap component, so acceptance criterion 18 is partial. (2) There is no signal cache; `backend/engine/runner.py` recomputes signals for each run even when `strategy_hash` is unchanged, so acceptance criterion 19 and `docs/BRIEF.md` §8.6 describe a target, not shipped behaviour.
## Why
- Calling a snapshot walk "L2 replay" or a one-axis table a "heatmap" would overstate what the code does and weaken every other evidence claim in the dashboard.
- `AGENTS.md` §2 forbids invented numbers and profit claims; the same honesty applies to capability claims in docs.
## Watch out
- When updating the brief or changelog, keep the partial criteria labelled partial until the heatmap and signal cache exist. Do not silently promote them in the session master.
- Book on a pre-2026 run still resolves to Conservative with `reason = no_book_coverage`; that fallback is not a substitute for replay.
