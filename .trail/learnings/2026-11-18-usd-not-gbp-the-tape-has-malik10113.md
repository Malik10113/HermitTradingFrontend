---
title: "USD, not GBP: the tape has no verified exchange rate"
author: Malik10113
date: 2026-11-18
branch: docs/v1
id: l-28e934b4
scope: repo
status: active
src: agent
touches:
  - docs/IMPLEMENTATION.md
  - docs/SESSION_MASTER_2026-11-18_V1.md
  - backend/rules/schema.py
  - backend/engine/runner.py
---

## Decided
- v1 sizes, charges, settles and reports in **USD**. The UI labels stake, bankroll and P&L in USD; the engine starts a CASH account with `Currency("USD")`.
- `bankroll_gbp` survives only as a legacy input alias in `backend/rules/schema.py` (`AliasChoices("bankroll_usd", "bankroll_gbp")`) and is normalised to `bankroll_usd`. There is no stored field called GBP.
- This corrects the £ wording in `docs/BRIEF.md` rather than converting it.
## Why
- The Polymarket tape carries `usd_amount`; the acquired data has no verified GBP/USD rate, timestamp or source. A £ label with a made-up rate would violate the no-invented-numbers rule in `AGENTS.md` §2.
- API money fields use `_usd` and costs use `_bps`, so a single currency keeps the contract unambiguous.
## Watch out
- Do not add an FX assumption to make the old £ labels true, and do not re-introduce a `bankroll_gbp` output field. If GBP display is ever wanted, it needs a registered rate source first.
