---
title: "MVP is Polymarket-only; BinaryOption per token, short YES means buy NO"
author: Malik10113
date: 2026-11-18
branch: master
id: l-25757327
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - docs/BRIEF.md
---

## Decided
- MVP reads Polymarket only. Kalshi and Betfair readers/adapters are v2 and must not be added before their adapter lands (docs/BRIEF.md, AGENTS.md).
- Model one Nautilus `BinaryOption` per outcome token: `token1` YES and `token2` NO are separate instruments.
- "Short YES" in the UI compiles to "buy NO". Never simulate a naked short, and always show the token actually traded.
## Why
- One venue keeps bar construction, token mapping and settlement unambiguous for the first working dashboard.
- Polymarket has no naked shorting; mapping the intent to the NO token is the only faithful model.
## Tried and ruled out
- One YES instrument with sell orders: would misrepresent Polymarket mechanics and break settlement accounting.
