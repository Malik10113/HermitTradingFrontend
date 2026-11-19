---
title: "2026 in-play coverage: the tape stops on 2026-10-05, bars are faithful"
author: Malik10113
date: 2026-11-19
branch: master
cost: { minutes: 45 }
id: l-38354211
scope: repo
status: active
src: agent
touches:
  - backend/data/bars.py
  - docs/DATA_TRACE.md
  - frontend/src/components/PriceChart.tsx
---

## Discovered
- Both Polymarket tapes end at 2026-10-05T01:59Z, while the results/events spine and the
  website continue past 2026-10-18. Of 989 2026 match keys with a lifecycle row, 934 have at
  least one in-play fill and 50 are dated after the tape max with pre-match fills only, so
  their charts cannot show in-play bars.
- Bar construction is faithful: for `epl-bou-mac-2026-05-19` the 20:00–20:26 UTC 5m buckets
  match Polymarket Data API v2 last trades exactly (6/6), and the broad 5m sample differs by
  0.00385 on average.
- For `epl-liv-mac-2026-10-11` Polymarket has 1,415 YES trades during the match, while our
  last fill is 2026-10-04T23:07Z and the in-match bar count is 0, because the tape stops a week
  earlier.

## Why
The chart is built only from the local tapes by design, so the missing in-play candles are a
source-acquisition gap, not a bar-builder bug; synthesising candles from the live API would
break the read-only, reproducible data version contract.

## Watch out
- Do not "fix" the 55 no-in-play matches by inventing bars; extend the sibling acquisition past
  2026-10-05 (or add a registered v2 source) and rebuild.
- The Data API v2 prices-history is 5m for recent resolved markets and 12h for older ones, so
  compare at the resolution the endpoint actually returns.
