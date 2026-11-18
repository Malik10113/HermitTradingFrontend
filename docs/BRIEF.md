# HermitTradingFrontend — project brief

> A single-user, local-first research dashboard for prediction-market OHLC data,
> dynamic technical indicators, and NautilusTrader backtests, reachable over
> Tailscale.

Status: design agreed 2026-11-18. Implementation not started.

## 1. Purpose

Turn the acquired prediction-market price data into a legible research surface:

1. Browse what data exists, for which venue / league / market / selection.
2. See it as OHLC candles with volume, at time and non-time bar resolutions.
3. Add, remove and parameterise technical indicators from the UI.
4. Compose a strategy from those indicators, or start from a built-in.
5. Run a deterministic backtest through NautilusTrader and inspect the result.

The dashboard is a research instrument, not a tipster. It makes no profitability
claim, surfaces no live prices beyond the stored data, and never places an order.

## 2. User and operating model

- One user (the owner), one machine, one browser.
- Served on the tailnet only. No public internet exposure.
- Data is read-only from `/root/FundedBetBacktesting/data`; the app writes only
  to its own `var/` directory.
- Long-running backtests are queued and run in isolated worker processes.

## 3. Scope

### In scope (v1)

- Venue: **Polymarket only** for the MVP. Kalshi is deferred to v2 even though
  its one-minute quotes are easy to ingest; keeping one venue keeps the bar
  semantics, token mapping and settlement path unambiguous. Betfair is also v2
  because its prices are decimal odds (1.01–1000), not 0–1 probabilities.
- Binary contracts priced 0–1, settling to 0 or 1.
- 1-minute time bars built from Polymarket fills; 5m/15m/1h/1d derived by
  aggregation.
- Non-time bars: tick, volume, value (dollar) and Renko, built from trade ticks.
- Technical indicators computed server-side with Nautilus indicator classes.
- Strategy authoring: curated built-in catalogue + declarative rule builder.
- Backtests: one instrument per run, long and short, explicit fees/slippage,
  hold-to-settlement or rule-based exit.
- Execution profiles: **Frictionless**, **Optimistic**, **Conservative**
  (default), **Stress**, and **Book**. Book prices from the L2 book and falls
  back to Conservative when coverage or a fresh snapshot is absent.
- Results: metrics, equity curve, drawdown, trade list, chart markers.
- Reproducible runs: stored config, data version, code version and artifacts.

### Out of scope (v1)

- Live order placement, execution, account management.
- Multi-instrument portfolio backtests (planned v2).
- Arbitrary Python upload or in-browser code execution.
- Tick-by-tick L2 order-book replay (v2; L2 data exists but is not replayed).
- Kalshi and Betfair venue adapters (v2).
- Mobile-native app, multi-user accounts, public hosting.
- Range bars (Nautilus 2.x has no range aggregation).

## 4. Data sources

Authoritative inventory: `/root/FundedBetBacktesting/docs/DATA_REGISTER.md`.
This project registers only the datasets it actually reads, in its own
`docs/DATA_REGISTER.md`.

| Source | Path (under sibling `data/`) | Shape | Use |
|---|---|---|---|
| Polymarket football tape | `polymarket/football/football_moneyline_tape.parquet` | 7.76M fills, `price_yes`, `usd_amount`, `timestamp` | Build 1m bars, tick/volume/value/Renko bars |
| Polymarket EPL tape | `polymarket/epl/epl_moneyline_tape.parquet` | 3.52M fills | Same, EPL-only |
| Polymarket markets | `polymarket/sii/markets.parquet` | 4.05M market rows | Metadata, token mapping |
| Polymarket mapped events | `results/polymarket_events.parquet` | 1,961 rows | Match / settlement spine |
| Polymarket snapshots | `results/polymarket_snapshots.parquet` | 102k rows | 24h/6h/1h pre-match reference |
| Match results | `results/big4_matches.parquet` | 47,628 matches | Settlement outcomes |
| Polymarket books | `cache/orderbooks_compact/` | 17.2M L2 rows | v2 execution-aware work |
| ESPN key events | `events/espn/summary` | 2026 cache; `keyEvents` kickoff / end | Actual kickoff and match end for lifecycle phases |

The lifecycle dataset is derived in this repo as
`var/lifecycle/match_lifecycle.parquet`. It is built from ESPN `keyEvents`
entries of type `kickoff` and `end-regular-time`, using the `wallclock` field
(UTC). The 2026 cache covers 1,006 of 1,007 fixtures with both timestamps;
pre-2026 fixtures are backfilled through the ESPN scoreboard API. Where a
`wallclock` is absent, the row is omitted and the phase is `unknown`. A nominal
match duration is never substituted.

Deferred to v2: `kalshi/epl_2025/quotes.parquet`,
`kalshi/epl_2025/markets.parquet`, and the Betfair historic tree. They are
registered when their adapters land, not before.

Rules:

- Never write to the sibling repo. Open Parquet/DuckDB read-only.
- Every source has recorded license and provenance; ESPN, Betfair, Kalshi and
  Predexon data are personal-use and not redistributable.
- Data version = SHA-256 over the ordered list of
  `(relative path, size, mtime_ns, row count, min(ts), max(ts))` plus the
  builder version. Two runs with the same data version must produce identical
  bars.

## 5. Instrument and settlement model

- Nautilus `BinaryOption` is the instrument type; it explicitly supports
  prediction markets and yes/no contracts.
- **Confirmed 2026-11-18:** one instrument per **outcome token**, not per market.
  Polymarket `token1` (YES) and `token2` (NO) are separate `BinaryOption`
  instruments.
- Price range 0–1; Polymarket price precision (0.001) is recorded from the
  market metadata, never assumed.
- Order intents are explicit, because Polymarket treats YES and NO as separate
  assets and separate books:

  | Intent | Order |
  |---|---|
  | Open long YES | BUY YES |
  | Close long YES | **SELL YES** |
  | Open short YES | BUY NO |
  | Close short YES | **SELL NO** |

- Buying NO is not an exit for a YES position and is not equivalent to selling
  YES. Closing a long is an outright sell of the held token; closing a short is
  a sell of the NO token. A market sell is fill-and-kill at the bid.
- "Short YES" means "buy NO" for opening a short. It is not a naked short and
  the system never simulates one. The trade list shows the token and side
  actually traded.
- Expiry is the market's `end_date`; settlement value is 0 or 1 from the mapped
  results table. A run that holds to expiry settles; a run that exits early
  sells the token at the bid minus costs.
- Kalshi and Betfair are v2. Betfair in particular cannot share this model: it
  quotes decimal odds, so it will use `BettingInstrument` and a separate price
  scale.

### 5.1 Market lifecycle

Phases are derived from observed match timestamps, not from an assumed duration.

| Phase | Rule |
|---|---|
| `pre_match` | `t < actual_kickoff` |
| `in_play` | `actual_kickoff <= t < actual_end` |
| `post_match` | `actual_end <= t < settlement` |
| `settled` | `t >= settlement` |
| `unknown` | no verified lifecycle row |

`match_lifecycle` fields:

```
match_key, actual_kickoff_utc, actual_end_utc, source, precision
```

- Source of truth: ESPN `keyEvents` — `kickoff` and `end-regular-time` — with
  their UTC `wallclock`. `source = espn_keyevents`,
  `precision = observed`.
- Delayed kickoffs are handled naturally: the observed `kickoff` wallclock is
  used, not the scheduled `kickoff_utc`.
- If a fixture has no verified row, `phase = unknown`, the phase filter is
  disabled for it, and the UI says why. There is no 120-minute fallback.
- Extra time and penalty shootouts use the last terminal event where present;
  league matches end at `end-regular-time`.
- Lifecycle operands for the rule builder: `time_to_kickoff_minutes`,
  `time_since_kickoff_minutes`, `time_to_settlement_minutes`, `phase`,
  `market_status` (`open` | `closed` | `settled`).
- Pre-2026 coverage depends on the ESPN backfill. 2026 is complete from the
  existing fixture map. Default backfill range: Big-4 from 2022-11, EPL from
  2024-10, matching the trade tape.

## 6. Bar model

Canonical bar schema:

```
instrument_id, bar_type, ts_event, ts_init,
open, high, low, close, volume, trade_count,
source, data_version
```

- Timestamps are UTC nanoseconds. Naive datetimes are rejected.
- 1-minute bars are the base, written as `...-1-MINUTE-LAST-EXTERNAL`.
- Higher timeframes are composite bars aggregated by Nautilus:
  `...-5-MINUTE-LAST-INTERNAL@1-MINUTE-LAST-EXTERNAL`.
- Non-time bars are built from trade ticks: `TICK`, `VOLUME`, `VALUE`,
  `RENKO`, and the imbalance/runs variants. Range bars are not available.
- Polymarket has no native OHLC: bars are built from fills. Open/high/low/close
  are last-trade prices; volume is USD notional; `trade_count` is recorded.
- Kalshi (v2) ships one-minute quotes; its builder will use the same canonical
  schema, retaining bid/ask separately.
- Empty intervals produce **no bar**, not a zero-volume bar. Charts may
  forward-fill for display only; strategies never see a filled bar.
- A bar whose source data is stale is flagged `is_stale`; the UI shows the flag.

## 7. Indicator model

- One implementation: Python, wrapping Nautilus indicators. The chart and the
  backtest consume the same series; there is no JavaScript reimplementation.
- `IndicatorSpec` registry entry: id, label, pane (`overlay` | `separate`),
  inputs, parameters (type, min, max, default, step), outputs, warm-up bars.
- v1 set: SMA, EMA, WMA, RSI, MACD, Bollinger Bands, ATR, Stochastic, Donchian
  channels, VWAP.
- Prediction-market additions (P2/P4): clipped logit
  (`ln(p / (1 - p))`, with `p` clipped to `[eps, 1 - eps]`), absolute and
  percentage probability change, rolling probability volatility, distance from
  recent probability extremes, rolling volume, trade count, and time-to-event.
- Series are cached by `(data_version, bar_type, spec_hash)`; a parameter change
  is a new cache entry.
- Warm-up values are returned as nulls so the UI can distinguish "not ready"
  from "zero".
- A parity test pins every indicator against a golden fixture.

## 8. Strategy model

Two authoring paths, one execution model.

### 8.1 Built-in catalogue

Curated, versioned, each with a provenance tag. v1 ships:

| Id | Origin | Rule shape |
|---|---|---|
| `nautilus_ema_cross` | Nautilus `EMACross` example | Fast EMA crosses slow EMA |
| `sma_trend` | sibling `momentum_trend_follow` | Close vs SMA, trend filter |
| `rsi_reversion` | sibling `momentum_rsi_recovery` | RSI oversold recovery / overbought fade |
| `bollinger_reversion` | sibling `mean_reversion_pct_band_flip` | Band excursion then re-entry |
| `macd_trend` | sibling `momentum_macd_histogram` | MACD histogram sign and slope |
| `donchian_breakout` | sibling `momentum_breakout_band` | N-bar high/low breakout |
| `atr_vol_breakout` | sibling `vol_compression_breakout` | ATR compression then expansion |
| `vwap_reversion` | sibling `mean_reversion_volume_reverted` | Deviation from VWAP |
| `buy_and_hold` | sibling `always_back` baseline | Buy at first bar, settle at expiry |
| `random_entry` | sibling `pseudo_random_sides` baseline | Seeded null strategy |

Later: `flow_momentum` and `large_trade_follow` from
`polymarket_research/strategies/`, once trade-flow features are exposed.

### 8.2 Declarative rule builder

A strategy is JSON, not code:

```json
{
  "strategy": {
    "instrument_id": "…",
    "bar_type": "…",
    "indicators": [
      {"instance_id": "ema20", "type": "ema", "params": {"period": 20}},
      {"instance_id": "sma50", "type": "sma", "params": {"period": 50}},
      {"instance_id": "rsi14", "type": "rsi", "params": {"period": 14}}
    ],
    "long_entry": {"all": [
      {"crosses_above": ["indicator:ema20:value", "indicator:sma50:value"]},
      {"gt": ["indicator:rsi14:value", 50]}
    ]},
    "long_exit": {"any": [{"crosses_below": ["close", "indicator:sma50:value"]}]},
    "short_entry": null,
    "short_exit": null,
    "settlement": "hold_to_expiry"
  },
  "execution": {
    "profile": "conservative",
    "fill_timing": "next_bar_open",
    "fee_bps": 0,
    "slippage_bps": 10,
    "min_abs_slippage": 0.001
  },
  "risk": {
    "sizing": {"mode": "fixed_stake", "value": 100}
  },
  "evaluation": {
    "date_range": {"from": "2024-10-01", "to": "2026-10-01"},
    "split": {"train_end": "2026-04-01", "test_start": "2026-04-02", "embargo_days": 1}
  }
}
```

- Operands: price fields, `indicator:<instance_id>:<output>`, lifecycle
  variables and constants.
- Comparators: `gt`, `gte`, `lt`, `lte`, `crosses_above`, `crosses_below`.
- Boolean: `all`, `any`, `not`. Depth and node count are capped.
- Validation rejects unknown indicators, out-of-range parameters and empty
  rule trees before a job is created.
- The `strategy` section is canonicalised into `strategy_hash`; the whole config
  plus data and code versions is canonicalised into `run_hash`.
- No string code generation and no `eval`.

### 8.3 Execution

- One `RuleStrategy` Nautilus class interprets the validated JSON at runtime.
- Orders are market orders. Buys walk the ask side; sells walk the bid side and
  are fill-and-kill. Default fill timing is the next bar's open; the convention
  is configurable and recorded.
- The order side follows the intent table in §5: closing a long sells the held
  token, and opening a short buys the NO token. The fill engine never treats a
  complement purchase as an exit.
- Sizing is **Fixed stake (USD, the tape notional)** or **Risk % of bankroll**. Both show stake,
  contracts, maximum loss, maximum payout and maximum profit before the run.
- Fractional Kelly is deferred to v1.1. It needs a calibrated probability, and
  using the full-sample win rate would be lookahead.
- One position per instrument; no pyramiding in v1.

### 8.4 Execution profiles

Five profiles, one user-facing vocabulary. Each resolves to an internal cost
source (`none`, `fixed`, `dynamic`) that is recorded in the manifest but never
shown as a second set of labels.

| Profile | Cost source | Fee | Slippage | Minimum absolute | Use |
|---|---|---|---|---|---|
| **Frictionless** | `none` | 0 | 0 | 0 | Null baseline |
| **Optimistic** | `fixed` | venue | 0 | 0 | Best case |
| **Conservative** | `fixed` | venue | 10 bps | 0.001 | Default |
| **Stress** | `fixed` | venue | 25 bps | 0.002 | Robustness |
| **Book** | `dynamic` | venue | book walk | — | 2026 only; falls back to Conservative |

Fill price for a buy or sell on a token at price `c`:

```
slip      = max(c * slippage_bps / 10_000, min_abs_slippage)
buy_fill  = clamp(c + slip, tick, 1 - tick)
sell_fill = clamp(c - slip, tick, 1 - tick)
```

The same formula applies to YES and NO because each token is its own instrument;
the side comes from the open/close intent, not from the token name. Closing a
long sells the held token; opening a short buys the NO token. A complement
purchase is never treated as an exit.

**Book** adds:

- the latest L2 snapshot with `ts <= decision_ts`; no later snapshot may price a
  fill;
- buys walk asks, sells walk bids, up to `max_depth_levels` (default 10);
- impact = `(VWAP − mid) / mid` for the order size;
- partial fill at visible depth; the unfilled remainder is recorded, never
  assumed filled;
- venue fee from the configured schedule;
- no queue position. This is a bounded estimate, not a fill simulator.

**Fallback.** Book → Conservative. Never to Frictionless.

- **Run-level:** if the instrument or range has no book coverage, the run keeps
  `requested: book` but is priced as `effective: conservative`. The manifest
  records `reason: no_book_coverage` and the available coverage window. The
  preflight warns before submission.
- **Fill-level:** a missing or stale snapshot (older than `book_staleness_s`,
  default 60) prices that fill at Conservative. The trade row records
  `execution_source: conservative_fallback` and the reason; the summary reports
  the fallback rate.
- Invalid profile parameters reject the run. They never downgrade it to
  Frictionless.

**Cost reporting.** Results show gross and net metrics, a breakdown of fees,
spread and impact, total cost in bps, and the fallback rate. A result whose
effective profile differs from the requested profile carries a badge and a
one-line explanation.

### 8.5 Evaluation and diagnostics

**Chronological split.** `evaluation.split` defines train and test windows with
an optional embargo. No random split. Results report in-sample and out-of-sample
separately.

**Holdout ledger.** The out-of-sample window is hidden behind an explicit
"Reveal out-of-sample" action. Each reveal appends
`(universe, strategy_hash, split_hash, run_id, timestamp)` to an append-only
ledger. The first reveal is logged; the second and later carry a prominent
warning that the window is no longer clean. Changing execution, sizing or fees
does not reset the count; changing rules or indicator parameters creates a new
strategy hash but still increments the experiment's variant counter.

**Metrics.** Core: net P&L, return on initial capital, Sharpe (daily, 365-day
annualisation, rf = 0, flat days included), maximum drawdown, trade count, win
rate, average win/loss, profit factor, expectancy, average holding period,
exposure, fees and slippage. Prediction-market: average entry/exit probability,
average probability movement captured in YES-equivalent points, return per USD of
capital deployed, return per USD notional traded, average contracts, settlement
P&L vs trading P&L, held-to-settlement count, early exits. Gross and net are
shown together; slippage is embedded in the fill and fees are a separate line.

**Phase metrics.** Where the lifecycle row exists, results break down by
`pre_match`, `in_play` and `post_match`: trades, return and Sharpe. Fixtures
with `phase = unknown` are reported as unknown, not folded into a phase.

**Diagnostics.** Signals generated, orders submitted, fills, rejects, trades
completed, held to settlement, early exits, average time between signals,
average holding time, percentage of time in market, average entry/exit price,
and per-leaf condition truth counts.

**Data-quality preflight.** Every run shows, for the selected instrument and
range: available period, trade count, active-minute percentage, longest gap,
median trade size, and a quality grade. It warns — without blocking — on sparse
coverage, missing settlement, insufficient indicator warm-up, very few trades, a
long no-trade period, and zero signals generated. Acknowledged warnings are
recorded in the manifest.

**Experiments and sweeps.** An experiment is a parent containing standard child
runs, not an optimiser.

- Grid over one or two parameters; default maximum 50 runs / 15 minutes, hard
  caps 200 / 60 minutes; concurrency 1.
- Every variant is retained. Results show a table sortable by any metric and a
  2-D heatmap with a selectable metric; in-sample and out-of-sample columns
  appear where a split exists.
- Cancelling the parent cancels its children; progress is completed / total.
- The experiment records the variant count. It never auto-promotes a winner; the
  weakest evidence level governs.

### 8.6 Caching

| Component | Key |
|---|---|
| Bars | `instrument + bar_type + data_version` |
| Indicator series | `bars_key + spec_hash + indicator_impl_version` |
| Signals | `bars_key + strategy_hash + warmup + indicator_impl_version` |
| Settlement | `instrument + settlement_version` |
| Lifecycle | `match_key + lifecycle_version` |

Signals are independent of execution, sizing and the evaluation date, so a
sweep or a comparison reuses them. The full series is computed with a warm-up
buffer and sliced; it is not recomputed per date window. The cache is LRU with a
size cap and a stats endpoint.

```json
"execution": {
  "profile": "book",
  "book_staleness_s": 60,
  "max_depth_levels": 10,
  "fallback": "conservative"
}
```

## 9. Backtest lifecycle

1. `POST /api/backtests` validates the request and writes a `queued` row.
2. A worker subprocess builds/loads the catalog slice, runs
   `BacktestNode`, and writes artifacts.
3. The API exposes status and progress over SSE; the UI shows queued →
   running → complete/failed.
4. Artifacts per run: `config.json`, `summary.json`, `equity.parquet`,
   `trades.parquet`, `positions.parquet`, `signals.parquet`, `costs.parquet`,
   `diagnostics.json`, `log.txt`, `manifest.json`.
5. The manifest records the requested and effective execution profile, the
   fallback reason and rate, the lifecycle source/coverage, and the book
   coverage window used by the Book profile.
6. Cancellation sends SIGTERM; orphaned workers are reaped on startup.
7. Concurrency is capped (default 1) because Nautilus engines are not
   thread-safe; isolation is by process, not thread.

## 10. API surface (v1)

```
GET  /api/health
GET  /api/meta                       # data versions, catalog coverage
GET  /api/instruments                # filter: venue, league, date
GET  /api/instruments/{id}
GET  /api/instruments/{id}/quality   # coverage, gaps, trade stats
GET  /api/search                     # fuzzy team / market search
GET  /api/bars                       # instrument, bar_type, from, to, limit
GET  /api/indicators                 # registry
POST /api/indicators/preview         # spec + range -> series
POST /api/execution/preflight        # requested profile -> effective profile, coverage, cost estimate
GET  /api/strategies                 # built-ins + saved rules
POST /api/strategies                 # validate/save a rule
GET  /api/presets                    # saved experiment configurations
POST /api/presets
POST /api/backtests
GET  /api/backtests
GET  /api/backtests/{id}
GET  /api/backtests/{id}/events      # SSE
GET  /api/backtests/{id}/artifacts/{name}
POST /api/backtests/{id}/reveal-holdout
GET  /api/runs/compare?ids=          # 2-3 runs
GET  /api/experiments
POST /api/experiments                # parent with child runs
GET  /api/experiments/{id}
POST /api/experiments/{id}/cancel
DELETE /api/backtests/{id}
```

All responses are JSON with explicit units and UTC timestamps. Errors use a
stable `{code, message, details}` shape.

## 11. UI

Primary route: **`/` Research Workspace**. Secondary routes: **`/data`**,
**`/runs`**, **`/presets`**.

The workspace is one screen:

- **Market bar** — hierarchical, searchable selector (Ctrl+K) with league,
  match, selection and date; team search across competitions; available data
  range, coverage badge and quality grade; YES/NO toggle; remembered last
  selection.
- **Chart** — candles, volume, shared indicator instances, kickoff marker,
  phase shading, All / Pre-match / In-play / Post-match toggle,
  signal/fill/settlement markers, open-position shading, trade inspector.
- **Strategy rail** — built-in picker or rule builder, quick/advanced toggle,
  sizing panel, execution profile selector (Frictionless, Optimistic,
  Conservative, Stress, Book), preflight. Quick mode exposes the built-in's
  primary parameters, direction, stake and costs; advanced exposes the full rule
  tree. Switching preserves values where the fields overlap.
- **Results drawer** — collapsed until a run completes; metrics, equity,
  drawdown, trade list, cost breakdown, diagnostics, phase breakdown, holdout
  reveal, and compare (2–3 runs, metric table, equity overlay normalised to 100
  at the common start).

Secondary pages:

- **Data explorer** — venue, league, market, selection, coverage, gaps, quality.
- **Runs** — history with config hash, data version, status, compare selection.
- **Presets** — saved experiment configurations (instrument, bar type, range,
  indicator instances, strategy, sizing, execution and evaluation), no
  folders/projects.

Rules:

- The chart never computes indicator math; it renders server series.
- Clicking a trade marker opens the trade inspector: signal and fill times,
  side and token, entry/exit prices, gross and net P&L, fees, and the
  indicator/condition context at entry.
- Indicators added to the chart are the same instances available to the
  strategy builder, and vice versa. They are added by search, configured inline
  (parameters, colour, visibility) and removed from the same chip.
- Every number has a unit and a source (data version + timestamp).
- URL state encodes instrument, bar type, range, indicator instances, strategy,
  execution profile, split and run/compare ids, so a view is shareable and
  reproducible. The last workspace state (instrument, bar type, timeframe,
  indicators, strategy, stake, costs, chart layout and theme) is remembered and
  can be reset.
- The data-quality preflight is visible before a run: available period, trade
  count, active minutes, longest gap, median trade size and quality grade, plus
  any warnings.
- Empty, missing-data and unknown-phase states are explicit; no fabricated
  candles and no inferred phase.
- The execution profile is visible on the form, the run row and the results
  page. Book preflight warns before submission when the run will fall back to
  Conservative.
- Gross and net metrics are shown together; net is never shown without its
  profile badge.
- The palette is colour-blind safe; candles remain distinguishable in dark and
  light themes.
- Keyboard navigation for the chart range, indicator panel and command palette.

## 12. Architecture

```
Browser (React + TS + Vite, lightweight-charts)
        │  HTTPS over tailnet
        ▼
FastAPI (127.0.0.1:8787) ── serves built frontend at /
        │
        ├── DuckDB read model ── var/bars/**/*.parquet
        ├── Lifecycle service ── var/lifecycle/match_lifecycle.parquet
        ├── Indicator service ── Nautilus indicators + cache
        └── Job queue ── subprocess per backtest
                              │
                              ▼
                    NautilusTrader 2.x
                    ParquetDataCatalog (var/catalog)
                    BacktestNode + RuleStrategy
                              │
                              ▼
                    var/runs/{run_id}/artifacts
```

## 13. Hosting and security

- The app binds `127.0.0.1:8787`, never `0.0.0.0`.
- `tailscale serve --bg --https=443 http://127.0.0.1:8787` exposes it to the
  tailnet over HTTPS. No public port, no Funnel.
- Optional `APP_TOKEN` shared secret for defence in depth; the token is read
  from the environment, never committed.
- CORS is unnecessary in production (same origin); the Vite dev server proxies
  `/api` to the backend.
- No credentials for venues are stored: v1 does not trade.

## 14. Reproducibility and provenance

Every run records:

- canonical strategy JSON + SHA-256 (`strategy_hash`);
- the complete experiment config + SHA-256 (`run_hash`), which includes
  strategy, execution, risk, evaluation, data version and code version;
- data version, lifecycle version and the exact source paths;
- bar type and aggregation chain;
- execution: requested and effective profile, fallback reason and rate, profile
  parameters, book coverage window, snapshot staleness bound;
- evaluation: date range, train/test split, holdout reveal count;
- code version (git SHA) and NautilusTrader version;
- artifact hashes.

The UI shows the data version, code version, effective execution profile and
holdout state on the results page. A run whose data version is no longer present
is marked `orphaned`, not silently re-run.

## 15. Performance targets

- Chart query: p95 < 300 ms for 20k bars from a warm DuckDB.
- Indicator preview: p95 < 500 ms for 20k bars and 8 indicators.
- Backtest: 1 instrument, 1 year of 1m bars, < 30 s.
- UI: first meaningful paint < 1.5 s on the tailnet.

## 16. Milestones

0. **Scaffold** — repo, AGENTS.md, trail, test runner, data register, config
   split and hashes.
1. **Foundation** — Polymarket bar builder, data versioning, catalog writer,
   instrument index with search and quality, workspace shell, chart, shared
   indicator instances, URL/default state, bar and indicator caches, 2026
   lifecycle source.
2. **First end-to-end run** — quick/advanced authoring, five execution profiles,
   sizing panel, single-run backtest, diagnostics, signal/fill markers, trade
   inspector, core metrics.
3. **Evaluation** — chronological split and holdout ledger, lifecycle backfill
   and phase metrics, compare runs, experiment presets.
4. **Scale** — experiment queue, bounded grid/heatmap, prediction-market
   indicators, settlement cache, cache stats/LRU.
5. **Hardening** — Tailscale serve unit, cancellation, orphan cleanup,
   acceptance suite, performance budgets.

## 17. Acceptance criteria (v1)

1. From a cold start, the dashboard lists every registered market and its
   coverage without reading the sibling repo's code.
2. Selecting an instrument and range renders candles that match a DuckDB query
   on the same bars, bar for bar.
3. Adding an EMA(20) overlay and running a backtest that uses EMA(20) produces
   the same indicator values in both (parity fixture).
4. A saved rule can be reloaded from its URL and reproduces the identical run
   hash.
5. A backtest shows equity, drawdown, trade list and chart markers; every
   number is traceable to an artifact.
6. The app is reachable at `https://<host>.<tailnet>.ts.net` with no listener
   on a public interface.
7. Killing the server during a run leaves no orphaned worker after restart.
8. A Frictionless run reports zero fees and zero slippage and carries the
   frictionless label.
9. A Conservative run charges `fee_bps` plus
   `max(price × slippage_bps / 10_000, min_abs_slippage)` per side on a golden
   fixture, and the UI shows the equivalent price points.
10. A Book run on pre-2026 data is recorded as effective `conservative` with
    `reason: no_book_coverage`; it is never silently priced with books.
11. Inside a Book run, a stale or missing snapshot prices only that fill at
    Conservative, and the trade row plus the run summary report the fallback.
12. A Book fill never uses a snapshot later than the decision timestamp
    (leakage test), and a partial fill at visible depth leaves the remainder
    unfilled.
13. Closing a long is a SELL of the held token; opening a short is a BUY of the
    NO token. The trade list shows the actual side and token, and a complement
    purchase is never treated as an exit.
14. Lifecycle phase comes only from `match_lifecycle`. A fixture without a
    verified row reports `unknown` and disables the phase filter; a delayed
    kickoff uses the observed kickoff, never a nominal duration.
15. The first out-of-sample reveal is logged; the second warns. Changing
    execution or sizing does not reset the count; changing the strategy hash
    increments the experiment's variant counter.
16. Two or three runs can be compared side by side with an equity overlay; the
    same strategy hash under different profiles shows different net costs.
17. The data-quality preflight shows available period, trade count, active
    minutes, longest gap, median trade size and grade, and warns on sparse data,
    missing settlement, insufficient warm-up and zero signals; acknowledged
    warnings appear in the manifest.
18. A two-parameter sweep produces a heatmap, retains every variant, and
    cancelling the parent stops all children.
19. Re-running the same strategy under a different execution profile reuses the
    cached signals: the strategy hash is unchanged and only the net costs move.
20. A URL restores the instrument, bar type, range, indicators, strategy,
    execution profile and run id; reset returns the remembered defaults.

## 18. Risks

| Risk | Mitigation |
|---|---|
| Prediction-market prices are not a continuous tradable series | Model tokens explicitly; no bar when no trade; settlement at expiry |
| Venue bar semantics diverge when Kalshi lands | One canonical schema with `source`, plus per-venue builder tests in v2 |
| Token mapping is wrong (YES/NO swapped) | Assert `token1`/`token2` against market metadata; settlement reconciliation test |
| Chart and backtest indicator drift | Single Python implementation + parity fixtures |
| Nautilus 2.x is a pre-release (rc9) | Pin the exact version; adapter boundary isolates upgrades |
| Long backtests block the API | Process isolation, queue, SSE, cancellation |
| Book execution model overstates achievable fills | Walk visible depth, no queue assumptions, partial fills, publish fallback rate |
| Stale book snapshot prices a fill | `book_staleness_s` bound; stale → Conservative fallback, recorded per trade |
| Lifecycle source is missing for a fixture | `phase = unknown`, filter disabled, no nominal duration; visible in the UI |
| Repeated holdout testing contaminates the test | Append-only ledger, one-shot notice, variant counter, warning on reuse |
| Complement purchase confused with a position exit | Explicit intent → order-side mapping; trade list shows side and token |
| Cost model dominates a weak signal | Report gross and net together; never show net without the profile badge |
| Data licences forbid redistribution | Data stays outside the repo; read-only; register records terms |

## 19. Open decisions

- Whether Betfair is normalised to probability for charting before its v2
  adapter lands.
- How far back the ESPN lifecycle backfill goes. Default: the same period as the
  tape — Big-4 from 2022-11, EPL from 2024-10.
- Whether Kalshi reuses the Polymarket `BinaryOption` mapping or brings its own
  settlement adapter (default: reuse the instrument model, separate adapter).
- Whether v2 portfolio backtests share one bankroll or run sleeve-per-instrument.
- Whether saved strategies are per-user files or SQLite rows (default: SQLite).
