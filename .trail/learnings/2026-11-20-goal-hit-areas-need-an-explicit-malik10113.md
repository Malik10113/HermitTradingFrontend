---
title: Goal hit areas need an explicit z-index above the chart canvas
author: Malik10113
date: 2026-11-20
branch: master
cost: { minutes: 15 }
id: l-70816e25
scope: repo
status: active
src: agent
touches:
  - frontend/src/components/PriceChart.tsx
---

## Discovered
- The goal flag/hit area was a sibling overlay after the chart container with
  `pointer-events-auto`, but the lightweight-charts canvas still intercepted Playwright's
  hover; jsdom passed because it has no paint order. The overlay container needed `z-10`,
  the flag `z-20`, and the popup `z-30` to paint and receive pointer events above the canvas.
- Phase/position bands must stay inside the non-interactive child; only the small flag should
  be interactive, otherwise the overlay swallows chart panning.

## Why
The browser resolves hit-testing by paint order, not DOM order, and the chart canvas creates
its own layer; without an explicit stacking value the overlay is behind it and the popup can
never be hovered despite the tests passing.
