# AGENTS.md

> **HermitTradingFrontend** — a single-user, local-first dashboard for
> prediction-market OHLC data, dynamic technical indicators and NautilusTrader
> backtests, served on the tailnet.
>
> The authoritative design is [`docs/BRIEF.md`](docs/BRIEF.md). This file is the
> working contract: what the project is, what an agent may change, and how a
> change is judged. When the two disagree, the brief wins and this file is
> corrected.

## 1. What this project is

A research instrument, not a trading system. It reads acquired prediction-market
data, renders it as candles, lets the user add indicators and compose a
strategy, then runs a deterministic NautilusTrader backtest and shows the
result.

The user is the owner, on one machine, over Tailscale. There is no sign-up, no
multi-tenancy and no public exposure.

## 2. Non-negotiable boundaries

- **Read-only data.** `/root/FundedBetBacktesting/` and its `data/` are never
  written to, moved or deleted. Open Parquet/DuckDB read-only. All generated
  state goes under `var/`.
- **No live trading.** No order placement, no venue credentials, no account
  management, no fund movement. Backtests only.
- **No arbitrary code.** Strategies come from the built-in catalogue or the
  declarative rule schema. The UI never uploads or evaluates Python. No `eval`,
  no string code generation.
- **No invented numbers.** Every price, metric, probability and timestamp shown
  must come from stored data or a computed artifact. If a value is missing, show
  "not available"; never interpolate, estimate or hard-code a plausible number.
- **No profit claims.** The dashboard reports historical simulation output. It
  does not say a strategy "works", "should" be traded, or will be profitable.
- **UTC only.** All timestamps are timezone-aware UTC; naive datetimes are a
  bug. Nanoseconds at the engine boundary, ISO-8601 in JSON.
- **Tailnet only.** Bind `127.0.0.1`; expose with `tailscale serve`. Never bind
  `0.0.0.0`, never enable Funnel, never add a public URL.

## 3. Stack and layout

| Area | Choice |
|---|---|
| Frontend | React + TypeScript + Vite, TradingView `lightweight-charts`, Tailwind CSS |
| Backend | Python 3.13+, FastAPI, Uvicorn, Pydantic v2 |
| Engine | NautilusTrader 2.x, pinned exactly (currently `2.0.0rc9`) |
| Data | Parquet + DuckDB; Polars for transforms |
| Env | `uv` for Python, `npm` for Node |
| Storage | SQLite for app state; Parquet for bars, runs and artifacts |

```
frontend/            React app; presentation and input validation only
backend/             FastAPI app, API schemas, job orchestration
backend/engine/      Nautilus adapter: instruments, catalog, RuleStrategy, runner
backend/data/        Bar builders, data-version hashing, DuckDB read model
backend/indicators/  IndicatorSpec registry and Nautilus replay adapter
docs/                BRIEF.md, DATA_REGISTER.md, CHANGELOG.md, session masters
scripts/             Operational entry points (build bars, build catalog, test)
var/                 Generated: bars/, catalog/, runs/, app.db (gitignored)
tests/               Domain-scoped tests (see §10)
```

Rules:

- `frontend/` never talks to Nautilus or reads Parquet. It calls the API.
- `backend/engine/` is the only place that imports `nautilus_trader`.
- A change that crosses a boundary updates the boundary's contract and its test.

## 4. Data contract

- The sibling data register is the upstream truth. This repo keeps its own
  `docs/DATA_REGISTER.md` listing only the datasets it reads, with path, license,
  row count, coverage and the rebuild command.
- Register a dataset before the first code path reads it.
- A **data version** is SHA-256 over ordered
  `(path, size, mtime_ns, rows, min_ts, max_ts)` plus the builder version.
  Store it on every bar file and every run. Same data version + same config +
  same code = identical result.
- Never silently fall back to a different source. If the expected path is
  missing, fail with a clear error naming the dataset and the register entry.
- Missing data is explicit: no bar for an empty interval, `is_stale` on a
  stale bar, and a visible gap in the chart.

v1 sources (see the brief for the full table):

- `polymarket/football/football_moneyline_tape.parquet`
- `polymarket/epl/epl_moneyline_tape.parquet`
- `polymarket/sii/markets.parquet`
- `results/polymarket_events.parquet`, `results/big4_matches.parquet`

Kalshi and Betfair are v2. Do not add their readers before the adapter lands.

## 5. Instrument and bar contract

- Instruments are Nautilus `BinaryOption` instances, one per outcome token.
- Prices are 0–1 probabilities. Venue precision is recorded, never assumed.
- "Short YES" compiles to "buy NO". The executed token is always visible.
- Settlement is 0 or 1 at expiry, from the mapped results table.
- Canonical bar fields: `instrument_id, bar_type, ts_event, ts_init, open,
  high, low, close, volume, trade_count, source, data_version`.
- 1-minute bars are the base (`...-1-MINUTE-LAST-EXTERNAL`). Higher timeframes
  are composite bars (`...-5-MINUTE-LAST-INTERNAL@1-MINUTE-LAST-EXTERNAL`).
- Non-time bars: `TICK`, `VOLUME`, `VALUE`, `RENKO`, and imbalance/runs
  variants. Nautilus 2.x has no range bars; do not promise them.
- Polymarket bars are built from fills: OHLC is last trade, volume is USD
  notional, and `trade_count` is retained.
- Kalshi (v2) will use the same canonical schema from its one-minute quotes,
  retaining bid/ask separately.
- No bar is fabricated for an interval with no trades.

## 6. Indicator contract

- One implementation: Python wrapping Nautilus indicator classes. The chart and
  the backtest use the same computed series. There is no JavaScript indicator
  math.
- Every indicator is an `IndicatorSpec`: id, label, pane, inputs, parameter
  schema (type, min, max, default, step), outputs, warm-up.
- v1: SMA, EMA, WMA, RSI, MACD, Bollinger, ATR, Stochastic, Donchian, VWAP.
- Cache by `(data_version, bar_type, spec_hash)`. A parameter change is a new
  entry, not a mutation.
- Return nulls during warm-up. Never return 0 for "not ready".
- Every indicator has a golden-fixture parity test. A chart value and a backtest
  value that disagree is a release blocker.

## 7. Strategy contract

Two authoring paths, one execution path.

**Built-ins** are curated and versioned, each with a provenance tag naming the
source file (sibling strategy or Nautilus example). v1 set: `nautilus_ema_cross`,
`sma_trend`, `rsi_reversion`, `bollinger_reversion`, `macd_trend`,
`donchian_breakout`, `atr_vol_breakout`, `vwap_reversion`, `buy_and_hold`,
`random_entry`.

**Rules** are declarative JSON:

- operands: price fields, `indicator:<id>:<output>` with parameters, constants;
- comparators: `gt`, `gte`, `lt`, `lte`, `crosses_above`, `crosses_below`;
- boolean: `all`, `any`, `not`, with capped depth and node count;
- entry/exit for long and short, sizing, costs, settlement.

Validation happens before a job exists: unknown indicator, out-of-range
parameter, empty tree or malformed operand returns `422` with a field path.
Canonicalise the JSON (sorted keys) and hash it; the hash identifies the run.

Execution:

- One `RuleStrategy` Nautilus class interprets the validated JSON at runtime.
- Market orders at the signal bar close, filled at the next bar open by default;
  the convention is configurable and recorded.
- Sizing: fixed stake, percent of bankroll, or fractional Kelly with a hard cap.
- One position per instrument; no pyramiding in v1.

### 7.1 Execution awareness

Three tiers. Every run records the **requested** and **effective** tier; the UI
shows both.

- `none` — zero fees and slippage. Frictionless research baseline, labelled as
  such. Never an automatic fallback.
- `fixed` — `fee_bps` + `slippage_bps` applied to entry and exit notional. Works
  on any period; this is the safe floor.
- `dynamic` — priced from the latest L2 snapshot with `ts <= decision_ts`:
  buy at ask / sell at bid, walk visible depth (default 10 levels), impact =
  `(VWAP − mid) / mid`, partial fill at visible depth. Requires 2026 book
  coverage. No queue position; this is a bounded estimate, not a fill simulator.

Fallback ladder: **dynamic → fixed**. Never fall back to `none`.

- Run-level: no book coverage for the instrument/range → effective `fixed`, with
  `reason: no_book_coverage` and the coverage window in the manifest. The API
  preflight warns before submission.
- Fill-level: missing or stale snapshot (older than `book_staleness_s`, default
  60) → that fill is priced at fixed, `execution_source: fixed_fallback`, and
  the summary reports the fallback rate.
- Invalid fixed parameters reject the run; they never downgrade it to none.

Results report gross and net, fees/spread/impact, total cost in bps, and the
fallback rate. Net is never shown without its tier badge.

## 8. Backtest contract

- Every run stores `config.json`, `summary.json`, `equity.parquet`,
  `trades.parquet`, `positions.parquet`, `costs.parquet`, `log.txt`,
  `manifest.json`.
- The manifest records: rule hash, data version, source paths, bar type and
  aggregation chain, requested/effective execution tier, fallback reason and
  rate, book coverage window, git SHA, Nautilus version, artifact hashes.
- Workers are separate processes. Nautilus engines are never shared across
  threads or requests. Concurrency defaults to 1.
- Queued → running → complete/failed/cancelled. Progress and logs stream over
  SSE; the UI never polls in a tight loop.
- Cancellation is SIGTERM, then SIGKILL after a grace period. Orphans are reaped
  on startup.
- A run whose data version is absent is `orphaned`; it is shown, not re-run.

## 9. API and UI contract

- API routes live under `/api`; the built frontend is served at `/`.
- Errors are `{code, message, details}` with a stable code.
- Units are explicit in field names (`_usd`, `_bps`, `_ns`) and documented.
- The chart renders server-computed series only. URL state carries instrument,
  bar type, range, indicator specs and run id, so a view is shareable and
  reproducible.
- `POST /api/execution/preflight` returns the requested/effective tier, book
  coverage and a cost estimate before a run is submitted.
- Every page shows the data version; every result page shows the run hash, the
  execution tier and, when they differ, the fallback reason.
- Empty, loading, stale and error states are designed, not incidental.
- Accessibility: keyboard navigation, colour-blind-safe palette, sufficient
  contrast in both themes.

## 10. Testing protocol

Domains: `frontend`, `api`, `engine`, `data`, `indicators`, `e2e`, `tooling`.
Run only the domains a change touches, plus their dependents. The runner is a
milestone 0 deliverable with this interface:

```bash
scripts/test_domains.sh --changed
scripts/test_domains.sh --changed --fast
scripts/test_domains.sh --domain engine
scripts/test_domains.sh --all          # before merge
```

Until it lands, run the suite for the area you touched directly:
`npm test` in `frontend/`, `pytest tests/<domain>` in the backend.

- Unit: Vitest for `frontend`; pytest for `backend`, `data`, `indicators`.
- E2E: Playwright against a built app with a small fixture catalog.
- Golden fixtures: one indicator fixture per indicator; one deterministic
  backtest fixture with a known equity curve and trade count.
- Tests never reach the network or the sibling repo. A test that needs data
  uses a checked-in fixture or skips with a reason.
- A test may not pass by mocking the thing under test.

## 11. Working rules

- Read `docs/BRIEF.md` and `docs/DATA_REGISTER.md` before changing data or
  strategy behaviour.
- Use trail before grepping: `trail ask "<task>" --source`. Save a learning when
  a decision, bug cause or correction would otherwise be lost. Run `trail check`
  before committing.
- Commit after each discrete change. Conventional messages. Never commit
  `var/`, credentials, or generated artifacts.
- Keep the sibling repo untouched; if its data is wrong, record it in this
  repo's data register and raise it with the user.
- Prefer boring, deterministic code. No hidden global state, no time-of-day
  dependence, no random seeds outside an explicit fixture.
- When a requirement is ambiguous, ask; do not guess a number, a venue rule or a
  cost assumption.
- Mode calls:
  - **defer**: use subagents to preserve context, push and update trails after
    each discrete change.
  - **wrapup**: write one master doc for the session, delete redundant session
    docs, update trails, push.

## 12. Decision log

| Date | Decision | Answer |
|---|---|---|
| 2026-11-18 | Data | Polymarket only for the MVP; Kalshi and Betfair are v2 |
| 2026-11-18 | Instrument model | One `BinaryOption` per outcome token; "short YES" = "buy NO"; no naked shorting (confirmed) |
| 2026-11-18 | Bars | 1-minute base with resampling, plus tick/volume/value/Renko |
| 2026-11-18 | Frontend | React + TypeScript + Vite |
| 2026-11-18 | Authoring | Declarative rules **and** a curated built-in catalogue sourced from the sibling strategies and Nautilus examples |
| 2026-11-18 | Indicators | Nautilus/Python for both chart and backtest |
| 2026-11-18 | Hosting | `tailscale serve`, tailnet-only |
| 2026-11-18 | Backtests | Single instrument, long and short |
| 2026-11-18 | Execution | None / Fixed / Dynamic; Dynamic prices from L2 and falls back to Fixed, never to None |

<!-- trail:start -->
## Trail — repo context graph

This repo is indexed in `.trail/cache/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `trail map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `trail ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `trail grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `trail skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `trail callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `.trail/cache/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `trail ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `trail build` (deterministic,
no API key, $0).
<!-- trail:end -->
