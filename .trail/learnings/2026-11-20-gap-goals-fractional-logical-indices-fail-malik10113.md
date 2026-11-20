---
title: "Gap goals: fractional logical indices fail, stack flags, show the market metric"
author: Malik10113
date: 2026-11-20
branch: master
cost: { minutes: 25 }
id: l-8825b1ae
scope: repo
status: active
src: agent
touches:
  - frontend/src/components/PriceChart.tsx
---

## Discovered
- In a sparse chart all five goals landed at `left: 0px`: `logicalToCoordinate(candidate + fraction)` returns 0 for fractional indices in the real lightweight-charts build, even though the mock interpolated them. The fix interpolates between the previous and next bar's `timeToCoordinate` x values instead; the five goals now sit at x 955-969 instead of 0.
- Candle markers only accept bar times, so the goal squares snapped to the last bar and piled up. Goals are now overlay flags/lines only; the canvas marker list carries just halftime/kickoff/end.
- A two-hour gap is one bar slot on this time scale, so five flags within 7px still overlapped; they are stacked at 18px rows by x proximity, and each flag has its own hover/focus popup.
- The OHLC chip now shows only `Goals N` for the moneyline market; corners remain in the API but are not a moneyline target and are no longer shown.

## Watch out
- Do not use fractional logical coordinates; use the bracketing bar coordinates.
- Keep the goal hit area at the flag, not full-height, or it swallows pan/crosshair.
