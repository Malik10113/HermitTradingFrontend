---
title: "Match markets expired at kickoff, so backtests stopped pre-match"
author: Malik10113
date: 2026-11-23
branch: master
cost: { minutes: 30 }
id: l-60df981c
scope: repo
status: active
src: agent
touches:
  - backend/engine/runner.py
  - tests/engine/test_runner_expiration.py
---

## Discovered
- Polymarket's match-market `end_date_utc` is the scheduled kickoff (e.g. 15:30 UTC), not the end of play. The runner passed it straight to the Nautilus `BinaryOption` expiration, so the engine stopped before kickoff and every trade was `pre_match`.
- The fix uses `max(market end, observed lifecycle end)` as the simulation expiration and settles at the observed end; the manifest now records `effective_expiration_utc` and `expiration_source`. A rerun of the existing 2026-05-10 West Ham-Arsenal config produced 75 trades with 2 in-play entries and 3 in-play exits; the old run had only pre-match trades.
- This is not a lifecycle-filter setting: the default filter is unset. The Racing YES/NO market still has zero second-half fills, so a backtest there will remain pre-match by data, not by engine cutoff.

## Watch out
- Old runs in the Runs tab were created before the fix and keep their pre-match trades; rerun the config to see in-play.
- Keep the market end when it is already later than the observed end (outright/season markets); only lift it to the observed match end.
