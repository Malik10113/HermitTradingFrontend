# Session master — 2026-11-27 (frontend, data coverage and in-play backtests)

> Supersedes `SESSION_MASTER_2026-11-19_V1_HANDOFF.md` for current state. The
> brief remains authoritative for design. This file records what changed between
> 2026-11-19 and 2026-11-27, what is deployed, what was measured, and what is
> still open.

## Goal

Close the user-reported frontend and data gaps (chart readability, indicator
panes, market labels, table sorting, 2026 coverage, goal markers, live match
state), then find out why every backtest appeared to end before kickoff. The
brief, not this file, remains the contract.

## Current state

| Item | Value |
|---|---|
| Build checkout | `/root/HermitTradingFrontend` |
| Production checkout | `/opt/hermit/prod`, detached at **`891d87c`** |
| Production state | `HTF_VAR=/opt/hermit/var` |
| Build/dev state | `HTF_VAR=/opt/hermit/var-dev` |
| Service | `hermit-frontend.service`, active, `127.0.0.1:8787` |
| Code version | `891d87c83c5359a9927d560454cceba252b9f043` |
| Data version | `sha256:0b9082529ffa9664f7acbc794e22a0454a72b8bde124dd5301526d87b0416b00` |
| Lifecycle rows | 1,881 (80 match keys still unknown) |
| 2026 instruments with 1m bars | 5,874 of 5,874 (2,439,296 bars) |
| Frontend tests | 212 passing |
| Backend | data, indicators, engine, api and tooling domains green |
| Tailscale Serve | local listener only; no public surface |

Master is docs-only ahead of production after this writeup; production serves
`891d87c`. `deploy/promote.sh` is the only supported build → production path.
Rollback target: `c63b8fc`.

## What shipped

### Workspace chart

- The OHLC/Vol/Fills legend follows the cursor and restores the last bar when the
  pointer leaves.
- Every indicator output has its own title and colour (MACD now renders
  `MACD`, `Signal`, `Histogram`), and every separate-pane indicator gets its own
  panned/zoomed chart with an independent price scale.
- Separate-pane indicators render even when no overlay indicator is selected;
  an empty pane says “Awaiting server values” or “No values in this range”
  instead of disappearing.
- The chart header shows kickoff, end, observed duration, total fills and total
  volume over the loaded range; the data hash moved to the chart footer.
- **Reset view** re-fits the chart zoom in place.
- Kickoff/end and halftime are labelled; goals are overlay flags with a
  team/time popup. Goal flags in a compressed gap stack vertically; a dashed
  line marks a goal in a bar gap. Goals can be toggled off; halftime stays.
- Goal popups show the ESPN match clock (`32'`, `90'+2'`) and the UTC wallclock.
  A hidden five-minute time ruler keeps sparse gaps proportional to time.
- The OHLC chip shows the market’s relevant metric only: `Goals N` for the
  moneyline market (corners remain in the API for future market types).
- **Match** range preset focuses the kickoff-to-end date window instead of the
  full multi-day coverage.

### Data and instrument UI

- Instrument labels read `Home vs Away — winner · YES/NO` from the preserved
  market leg; the question text remains the fallback when the match context is
  absent.
- Every data-table column header sorts the loaded rows: first click
  descending, second ascending; nulls stay last; ties break by instrument id.
- The data tab can load 100, 250, 500, 1,000, 2,000 or 5,000 markets. The choice
  is in the URL and the server query; the table still reports the true total.
- The application footer is pinned while any tab scrolls (`main` is the scroll
  container, so the footer no longer slides with the page).

### Lifecycle and in-play events

- The lifecycle API returns observed goals, halftime and corners. Goals come
  from `events/match_events.parquet`; corners and halftime come from the cached
  ESPN summary. Cards, VAR decisions, missed/saved penalties and nominal
  halftimes are never returned.
- The dataset is registered in `docs/data_register.json`; a missing registered
  source raises `MissingDatasetError` rather than silently returning no events.
- `match_state.goals`/`corners` use `null` for “source does not cover this
  match” and `[]` for a real zero.
- The 2026 goal clock (`32'`, `90'+2'`) comes from ESPN `clock.displayValue`;
  keyEvents and commentary are both scanned because keyEvents can omit a
  penalty goal.

### Backtests — the pre-match cutoff

`end_date_utc` for a match market is the **scheduled kickoff**, not the end of
play. The runner used it as the Nautilus `BinaryOption` expiration, so the
engine stopped before kickoff and every trade was `pre_match`.

Fix (`891d87c`):

- Effective expiration = `max(market end, observed lifecycle end)`.
- Settlement is recorded at the observed end for match markets; an outright
  market keeps its later market end.
- The manifest records `effective_expiration_utc` and `expiration_source`.

A rerun of the existing 2026-05-10 West Ham–Arsenal config produced **75 trades,
2 in-play entries and 3 in-play exits**; the old run had only pre-match trades.

## Data coverage and the two “missing data” questions

### 2026 bars

The tapes held 16.0M 2026 fills but only 40 bar files existed. All **5,874 2026
instruments** were pre-built at 1m: **2,439,296 bars, 0 failures**. The data
version and 1m coverage are in `/opt/hermit/var`, not git.

### Tape cutoff

Both Polymarket tapes end at **2026-10-05T01:59Z**, while the results spine and
the website continue past 2026-10-18. 50 of the 989 2026 matches with lifecycle
rows are dated after the cutoff; they have pre-match fills only, so no in-play
candles can exist. This is an upstream acquisition gap, not a bar-builder bug.

### Racing 19 Sep

The Racing YES/NO market (condition `0x04c7…8496`) has **zero** second-half
trades in both our tape and Polymarket Data API v2; the only two rows after the
final whistle are NO trades at 18:56:02/03 UTC. The match finished Celta 5–0, so
that market was effectively decided. This is the real Polymarket record, not a
gap in our construction:

- <https://polymarket.com/event/lal-cel-rrc-2026-09-19>
- <https://polymarket.com/event/lal-cel-rrc-2026-09-19/lal-cel-rrc-2026-09-19-rrc>

### Goal times and DST

Across **2,977** goal rows joined to their lifecycle:

- 0 goals before kickoff or outside the observed window;
- period 1: wallclock − (kickoff + match clock) = 0–511 s;
- period 2: 900–2,093 s (the observed halftime break);
- grouped by month, no 3,600 s shift.

No DST/timezone bug. The Celta goals are 32', 43', 69', 78' and 90'+2', matching
the ESPN summary; the apparent 89' was the wallclock difference across halftime.

## Verification

- Full domain suite green at `891d87c`; frontend **212** tests.
- Engine tests, including the new expiration cases, green.
- Browser checks at 1280×720 and smaller: footer pinned at every main-scroll
  offset on `/`, `/runs`, `/data` and `/presets`; data page 1,000 rows with
  `limit=1000`; goal popups for 32', 69' and 90'+2'; Match range sets the
  kickoff/end dates; separate RSI pane without an overlay.
- Data audit: 2,977 goals, 0 outside their match window; goal clocks checked
  against the ESPN summary.
- First real run after the expiry fix: `effective_expiration_utc =
  2026-05-10T17:33:34Z`, `expiration_source = observed_match_end`, 75 trades
  with in-play entries/exits.

## Known limitations and next steps

- **80 lifecycle unknowns** (26 unmatched ESPN scoreboard events, 53 summaries
  without a kickoff/end wallclock, 1 missing summary; 6 are 2026). They remain
  `phase = unknown`; no nominal duration is substituted.
- **50 late-2026 matches** have no in-play fills because the tape stops on
  2026-10-05. The sibling acquisition needs extending past that date (or the
  Data API v2 added as a registered v2 source) before those bars can exist.
- **Racing YES/NO** will still backtest pre-match because there were no
  second-half trades; pick a market with in-play fills (e.g. Celta YES/NO) to
  exercise in-play alpha.
- Old runs in the Runs tab were created before the expiry fix and stay
  pre-match; rerun the config to use the new expiration.
- Kickoff/end/halftime are still candle markers and snap to the nearest bar
  when their wallclock has no bar; goals are the only exact overlay.
- The data table sorts the loaded page only (up to 5,000 rows); no server-side
  sort or virtualised 5,000-row test yet.
- The goal overlay uses the chart’s five-minute ruler; a multi-day `All` view
  still compresses a match relative to the full range — use **Match**.

## Commit map (production cut `891d87c`)

| Area | Commits |
|---|---|
| Parallel skill and test scoping | `9261460`, `27526de` |
| Chart panes, colours, labels | `56d7693`, `f89a1ac`, `496ee2a`, `834105a`, `2e923b0` |
| Data table and page size | `a0fcc4d`, `34e98f9`, `83f613c` |
| Chart header, reset, footer | `9ce4858`, `b8c9d18` |
| Goals, halftime, live state, clocks | `96930a8`, `08eace9`, `af743bd`, `9edf645`, `4126bbc`, `8ee7fb1`, `c3b63cf`, `0656a9c`, `c02f983`, `fb36a3a`, `c63b8fc` |
| Backtest expiration | `891d87c` |
| Data coverage and trail | `769088f`, `6e260c3`, `4b489d3`, `68822bb` |

## Next step

Extend the sibling Polymarket tape acquisition past **2026-10-05** and rebuild
the 2026 1m bars; that is the only way to get in-play candles for the 50
late-season matches. Keep the 80 lifecycle unknowns as `unknown` unless a
deterministic ESPN event appears.
