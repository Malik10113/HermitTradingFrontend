---
title: Five execution profiles; kickoff from ESPN keyEvents
author: Malik10113
date: 2026-11-18
branch: master
id: l-4b9b0d01
scope: repo
status: active
replaces: l-6f19ef13
src: agent
touches:
  - AGENTS.md
  - TESTING.md
  - docs/BRIEF.md
  - docs/DATA_REGISTER.md
  - docs/DATA_TRACE.md
  - docs/data_register.json
---

## Decided
- `AGENTS.md` §7.1 and `docs/BRIEF.md` §8.4 use one user-facing execution vocabulary: Frictionless, Optimistic, Conservative, Stress, Book. There are no None/Fixed/Dynamic labels; the internal cost source is manifest-only.
- Book falls back to Conservative, never Frictionless.
- Closing a long sells the held token; opening a short buys NO. Buying NO is not an exit for YES, per Polymarket token semantics.
- `docs/BRIEF.md` §5.1 and `docs/DATA_TRACE.md` source lifecycle phases from ESPN `keyEvents` kickoff and end-regular-time wallclock. A missing row means `phase = unknown`; no nominal duration.
## Why
- Polymarket treats YES and NO as separate assets and supports outright sells; a complement purchase would leave the position open.
- One label set removes the tier/preset ambiguity. ESPN key events are the only observed kickoff/end source in the acquired data.
## Watch out
- Some historical ESPN events lack `wallclock`; do not infer a phase from scheduled kickoff or a fixed 120 minutes.
- Book uses L2 snapshots without queue position; it remains a bounded estimate.
