---
title: "Goal placement is wallclock-correct; no DST shift, but sparse bars compress the axis"
author: Malik10113
date: 2026-11-20
branch: master
cost: { minutes: 35 }
id: l-1d4c8c3a
scope: repo
status: active
src: agent
touches:
  - backend/data/events.py
  - frontend/src/components/PriceChart.tsx
  - frontend/src/pages/WorkspacePage.tsx
---

## Discovered
- Across 2,977 ESPN goal rows, 0 occur before kickoff or outside the observed match window; period-1 goals are 0-511 s after `kickoff + clock`, and period-2 goals are 900-2093 s after it (the halftime break). Grouped by month, no month shows a 3600 s shift, so the "wrong spot" is not DST or a timezone bug.
- The Celta 5-0 match exposed the display bug: the parquet `minute` is zero-based (31/42/68/77) and loses stoppage time, so the popups did not match ESPN's 32'/43'/69'/78'/90'+2. The cached summary `clock.displayValue` is authoritative; keyEvents omitted the 32' penalty, so both keyEvents and commentary are scanned.
- The visual pile came from the chart's bar-index x-axis: a two-hour no-trade gap is one slot, and a five-day `All` range makes the match a sliver. A five-minute whitespace ruler plus a `Match` range preset (kickoff..end date) spreads the goals; status is still a wallclock, so `18:01 UTC` is 69' after the halftime break, not 89'.

## Watch out
- Do not derive the goal minute from `wallclock - kickoff`; subtract the observed halftime break or use the ESPN display clock.
- The ruler is a live whitespace series; keep it hidden and do not add it to line-series option assertions.
