---
title: "Chart header, goals and footer: logical goal positions and a pinned footer"
author: Malik10113
date: 2026-11-20
branch: master
cost: { minutes: 60 }
id: l-19f4fe87
scope: repo
status: active
src: agent
touches:
  - frontend/src/components/PriceChart.tsx
  - frontend/src/components/layout/AppShell.tsx
  - frontend/src/pages/DataPage.tsx
  - frontend/src/pages/WorkspacePage.tsx
  - backend/data/events.py
---

## Decided
- Goal overlays are positioned by logical index (`logicalToCoordinate`), not
  `timeToCoordinate(goalTime)`, because lightweight-charts returns `null` for a time that is
  not an exact series point and only 32 of 1,917 goals land on a whole minute; the previous
  mock hid this by mapping any time linearly.
- The app footer is pinned by making `<main>` the scroll container with the footer in flow
  below it, because `sticky bottom-0` alone still let the footer clamp at y=423 on `/runs`
  (measured at 720 px viewport). The footer is global, so every tab is fixed at once.
- Live match state counts observed goals from `match_events` and corners parsed from the ESPN
  summary `commentary` (`corner-awarded` + `play.wallclock`), with `null` meaning the source
  does not cover the match and `[]` meaning a real zero; the UI prints `n/a` for `null`.
- The data tab page size is a bounded URL option (100–5000) because the API accepts
  `limit <= 5000`; the previous fixed 500 could not show the full inventory.

## Watch out
- The goal hit area is only the small top flag; do not make it full-height, or it swallows
  pan/crosshair below it.
- `timeScale().width()` clipping is required because logical coordinates can exceed the plot.
- The live state is a snapshot from the instrument detail request; it is not a streaming feed.
