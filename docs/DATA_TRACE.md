# Data trace: the pre/post-2026 distinction

> Quick reference for what the sibling repo actually holds either side of
> **2026-01-01**, and what that means for this dashboard. Compiled 2026-11-18
> from `FundedBetBacktesting/docs/DATA_REGISTER.md`,
> `docs/SESSION_MASTER_2026-10-26_GATES_EVENTS_CREATION.md`,
> `docs/CHANGELOG.md`, `docs/families/README.md`, the trail learnings, and a
> direct read of the parquet/DuckDB files.

## The short version

The cut is **depth, not price quality**. Pre-2026 has a clean trade-price tape.
It has no order-book ladder, so it cannot support execution, slippage or
queue-aware claims. 2026 is the only period with local L2 books, and even those
are snapshots without queue position.

The 2026 boundary is also a **collection boundary**: Predexon backfill starts
2026-01-01. It is not evidence that the market changed character on that date.

## What exists, by layer

| Layer | Coverage | Granularity | Pre-2026? | Post-2026? |
|---|---|---|---|---|
| Match results (`results/big4_matches.parquet`) | 1993-94 → 2026-27, 47,628 matches | Match score / result | Yes | Yes |
| Polymarket football tape | 2022-11 → 2026-10 (Big-4); EPL file starts 2024-10-08 | Fill: price, USD notional, token, maker/taker direction | Yes | Yes |
| Polymarket L2 books (`cache/orderbooks_compact`) | 2026-01-01 → 2026-10-21, 17.18M rows, 1,013 matches | L2 snapshot: best bid/ask, depth, ladder, no queue | **No** | Yes |
| Kalshi EPL L1 (`kalshi/epl_2025`) | 2025-05-11 → 2025-12-30, 579 markets / 193 matches | 1-minute `yes_bid` / `yes_ask`, no size, no ladder | Yes (2025 only) | Upstream 2026 exists (612/204), not local |
| ESPN in-play events (`events/match_events.parquet`) | 2026-01-01 → 2026-10-21, 7,678 events / 1,006 matches | Goals, cards, VAR; wallclock + 30 s publication bound | **No** | Yes |
| Betfair historic (`data/betfair`) | 2015-05 and 2015-06-12, 14,992 files | Last-traded price only | Neither useful side | Neither useful side |

Notes:

- `orderbooks_compact` has no 2026-06 or 2026-07 partitions. The Big-4 seasons
  are off in June–July; it is a seasonal gap, not missing data.
- The EPL tape holds 3,517,070 fills from 2024-10-08 to 2026-10-05.
- Kalshi is a **quote proxy**, not an exchange substitute: L1 only, no ladder,
  no size, no queue.
- Betfair's free historical tier is last-traded price; depth is a paid tier.

## Why the distinction matters

The sibling repo encodes the consequence in its own rules:

- **Execution-aware claims stay 2026-only.** Anything that needs slippage,
  spread, depth or queue is restricted to the 2026 Polymarket books.
- **Pre-2026 is development/validation data.** The tape is trade price, not an
  executable book.
- **Kalshi is signal/validation only.** It is not treated as an execution venue
  (worse depth/queue, no easier challenge).

Two findings that show the boundary in practice:

1. **Carry replication (pre-2026 development, 2026 holdout).** One band and one
   side were selected on the pre-2026 tape. The pre-registered extreme bands did
   not fire (B1 0.05–0.20 long mean +22.1%, match-clustered t +0.04; B5 0.80–0.95
   long mean −11.4%, t −0.05). The only significant bands were mid-bands the rule
   forbids selecting. No band → the 2026 panel was **not scored**. The family is
   closed.
2. **Cross-market consistency (2026 books).** Raw inconsistencies were
   placeholder/stale quotes; after the 60 s delay, 5-cent width cap and 500-share
   depth floor, only 7 post-hygiene violations and zero executable primary
   observations remained. The family is closed as inconclusive. So even 2026 L2
   needs hygiene before it is treated as executable.

## What the EPL tape quality report actually says

The 2026-10-19 report sampled 200 matches / 600 condition_ids:

- 942,934 fills; 593 markets; span 2024-10-10 → 2026-10-04.
- 0 monotonicity violations and 0 price-range violations (`price_yes` 0.001–0.999).
- 758/760 matches have exactly three moneyline legs; 2 have none
  (`epl-ast-liv-2026-05-15`, `epl-mac-cry-2026-05-13`).
- One row per fill, carrying both `maker_direction` and `taker_direction`;
  signed flow must use the taker/aggressor leg or it cancels to zero.
- 2,274 dated moneyline markets: 2,217 closed/resolved, 57 open at report time.

This is a price-data quality check, not an execution check. It does not create a
pre/post split.

## Implications for this dashboard (Polymarket MVP)

1. **Bars and backtests can use the full tape**, 2024-10 → 2026-10. The fills are
   clean enough for OHLCV construction.
2. **Execution awareness has three tiers.** `none` is an explicit frictionless
   baseline; `fixed` (absolute bps) works on any period; `dynamic` (L2 walk,
   spread, impact) is 2026-only and falls back to `fixed` with the reason
   recorded. A pre-2026 run can never be priced dynamically, and the UI must say
   so before submission.
3. **The MVP can ship without books.** v1 is price/indicator/backtest work;
   L2 replay is already v2.
4. **Show coverage, not just candles.** The UI must render the June–July seasonal
   gap and the pre-2026 "no depth" state explicitly.
5. **Never treat the 2026 cut as a regime change.** It is when Predexon started
   collecting. A strategy that "works only in 2026" may be measuring data
   availability, not market behaviour.

## Open caveats

- Predexon provides L2 snapshots, not full queue position; 2026 execution claims
  are still bounded.
- ESPN publication latency is a pre-registered 30 s bound, not a measured field.
- 57 moneyline markets were still open at the report date; settlement is pending
  for those.
- Kalshi's 2026 data exists upstream but is deliberately not part of the MVP.

## Sources

- `FundedBetBacktesting/docs/DATA_REGISTER.md` — paths, sizes, licences.
- `docs/SESSION_MASTER_2026-10-26_GATES_EVENTS_CREATION.md` §6 — coverage table.
- `docs/CHANGELOG.md` (2026-10-26) — Kalshi L1, no free pre-2026 depth.
- `docs/families/README.md` — pre-2026 data status.
- `.trail/learnings/2026-10-26-no-free-pre-2026-football-depth-malik10113.md`
- `.trail/learnings/2026-10-26-pre-2026-tape-selects-no-carry-malik10113.md`
- `reports/polymarket/epl_data_quality.md` — 2026-10-19 quality and leakage report.
- Direct reads: `epl_moneyline_tape.parquet` (3,517,070 rows),
  `orderbooks_compact` (2026-01…05, 08…10).
