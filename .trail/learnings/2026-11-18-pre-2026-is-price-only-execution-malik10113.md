---
title: Pre-2026 is price-only; execution features are 2026-only
author: Malik10113
date: 2026-11-18
branch: master
id: l-0b27ca8e
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
---

## Decided
- `AGENTS.md` and `docs/DATA_TRACE.md` define the pre/post-2026 split as a depth boundary, not a price-quality boundary. Pre-2026 Polymarket has a clean fill tape but no L2 book; any slippage, spread, depth or fill-probability feature is 2026-only and must be labelled as such.
- MVP bar/indicator/backtest work may use the full tape (EPL 2024-10-08..2026-10-05; Big-4 from 2022-11). L2 replay stays v2.
- Render coverage explicitly: the 2026-06/07 orderbook gap is the Big-4 off-season, not missing data.
## Why
- The 2026-01-01 cut is when Predexon collection starts, so a strategy that works only in 2026 may be measuring data availability rather than market behaviour.
## Watch out
- Even 2026 L2 is snapshots without queue position; execution claims remain bounded.
