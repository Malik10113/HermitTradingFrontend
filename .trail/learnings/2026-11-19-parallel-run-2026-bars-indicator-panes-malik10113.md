---
title: "Parallel run: 2026 bars, indicator panes, lifecycle source gap"
author: Malik10113
date: 2026-11-19
branch: master
cost: { minutes: 105 }
id: l-ed80a1a0
scope: repo
status: active
src: agent
touches:
  - frontend/src/components/PriceChart.tsx
  - frontend/src/api/normalise.ts
  - frontend/src/pages/DataPage.tsx
  - backend/data/lifecycle.py
---

## Decided
- Prebuilt 1m bars for all 5,874 2026 instruments into `/opt/hermit/var` (2,439,296 bars, 0
  failures) because only 40 bar files existed while the tape held 16.0M 2026 fills, so the
  charts opened empty and looked like a data-quality problem even though the source was full.
- Reverted the index-based lifecycle resolver: `polymarket_events.parquet` already lists 1,961
  match_keys against 1,937 in the instrument index, so it added `extra_fixtures: 0` and would
  have shipped dead code. The 80 series still missing a lifecycle row are a source gap: 26 have
  no unique ESPN scoreboard event on their date (or ±1 day), 53 have summaries whose
  `kickoff`/`end-regular-time` wallclock is absent, and 1 has no summary. Leaving those as
  `phase = unknown` is correct because a nominal duration would be invented.
- Chart panes now sync by visible **time** range and line colours are allocated globally across
  all indicators, because per-pane `fitContent()` misaligned a warmed-up series and reusing the
  instance hue made MACD's three outputs one colour.
- Instrument labels derive the leg from the raw `selection` preserved as `outcome` before
  normalisation overwrites `selection` with YES/NO, because otherwise the new
  `Home vs Away — winner` branch was unreachable from every caller.

## Watch out
- The 80 lifecycle gaps are honest unknowns; do not “fix” them with scheduled kickoff or a
  nominal match length. Re-check the ESPN source before attempting a backfill.
- 2026 bar coverage is data under `/opt/hermit/var`, not git; a fresh production `var/` needs
  `scripts/build_bars.py` re-run.
