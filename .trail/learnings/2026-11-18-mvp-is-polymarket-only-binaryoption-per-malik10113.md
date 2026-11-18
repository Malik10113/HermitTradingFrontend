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
- Closing a long sells the held token (SELL YES/NO); only opening a short buys the complement.
## Why
- One venue keeps bar construction, token mapping and settlement unambiguous for the first working dashboard.
- Polymarket has no naked shorting; mapping the intent to the NO token is the only faithful model.
## Tried and ruled out
- Modeling short exposure as a naked sell of a single YES instrument: ruled out. This is not the same as selling an owned YES token to close a long, which Polymarket supports and which the engine uses.
