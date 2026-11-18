---
title: "Execution tiers: none/fixed/dynamic, dynamic falls back to fixed"
author: Malik10113
date: 2026-11-18
branch: master
id: l-6f19ef13
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - TESTING.md
  - docs/BRIEF.md
  - docs/DATA_TRACE.md
---

## Decided
- `docs/BRIEF.md` §8.4 and `AGENTS.md` §7.1 define three execution tiers: `none` (zero cost, explicit baseline), `fixed` (`fee_bps` + `slippage_bps`), `dynamic` (L2 walk using the latest snapshot with `ts <= decision_ts`, visible depth, partial fills).
- Fallback is dynamic -> fixed only, never to none. Run-level no coverage gives effective `fixed` with `reason: no_book_coverage`; a stale or missing snapshot gives a per-fill `fixed_fallback`.
- Every run persists requested vs effective tier, book coverage window and fallback rate in the manifest.
## Why
- Pre-2026 has no L2 books. Silently pricing a dynamic run or dropping to zero costs would misstate results.
## Watch out
- Dynamic is 2026-only and has no queue position; it is a bounded estimate, not a fill simulator.
