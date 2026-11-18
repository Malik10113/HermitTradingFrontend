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

- Venues: Polymarket and Kalshi. Betfair is a later adapter because its prices
  are decimal odds (1.01–1000), not 0–1 probabilities.
- Binary contracts priced 0–1, settling to 0 or 1.
- 1-minute time bars built from trades/quotes; 5m/15m/1h/1d derived by
  aggregation.
- Non-time bars: tick, volume, value (dollar) and Renko, built from trade ticks.
- Technical indicators computed server-side with Nautilus indicator classes.
- Strategy authoring: curated built-in catalogue + declarative rule builder.
- Backtests: one instrument per run, long and short, explicit fees/slippage,
  hold-to-settlement or rule-based exit.
- Results: metrics, equity curve, drawdown, trade list, chart markers.
- Reproducible runs: stored config, data version, code version and artifacts.

### Out of scope (v1)

- Live order placement, execution, account management.
- Multi-instrument portfolio backtests (planned v2).
- Arbitrary Python upload or in-browser code execution.
- Tick-by-tick L2 order-book replay (v2; L2 data exists but is not replayed).
- Betfair venue adapter (v2).
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
| Kalshi EPL quotes | `kalshi/epl_2025/quotes.parquet` | 1.06M one-minute rows | Native 1m OHLC, bid/ask |
| Kalshi markets | `kalshi/epl_2025/markets.parquet` | 579 markets | Metadata, settlement |
| Match results | `results/big4_matches.parquet` | 47,628 matches | Settlement outcomes |
| Polymarket books | `cache/orderbooks_compact/` | 17.2M L2 rows | v2 execution-aware work |

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
- One instrument per **outcome token**, not per market:
  - Polymarket: `token1` (YES) and `token2` (NO) are separate instruments.
  - Kalshi: the YES and NO sides of a market are separate instruments.
- Price range 0–1; price precision comes from the venue (0.001 Polymarket,
  0.01 Kalshi) and is recorded, never assumed.
- "Short YES" in the UI means "buy NO"; it is not a naked short. The compiler
  translates the intent and the trade list shows the token actually traded.
- Expiry is the market's `end_date`; settlement value is 0 or 1 from the mapped
  results table. A run that holds to expiry settles; a run that exits early
  closes at the bar price minus costs.
- Betfair is excluded from v1 precisely because odds ≠ probability. Its adapter
  will use `BettingInstrument` and a separate price scale.

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
- Kalshi ships one-minute quotes: OHLC uses the quote price and bid/ask are
  retained separately for execution-aware work.
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
  "instrument_id": "…",
  "bar_type": "…",
  "long_entry": {"all": [
    {"crosses_above": ["indicator:ema:value", "indicator:sma:value"]},
    {"gt": ["indicator:rsi:value", 50]}
  ]},
  "long_exit": {"any": [{"crosses_below": ["close", "indicator:sma:value"]}]},
  "short_entry": null,
  "short_exit": null,
  "sizing": {"mode": "fixed_stake", "value": 100},
  "costs": {"fee_bps": 0, "slippage_bps": 10},
  "settlement": "hold_to_expiry"
}
```

- Operands: price fields, indicator outputs (with parameters), constants.
- Comparators: `gt`, `gte`, `lt`, `lte`, `crosses_above`, `crosses_below`.
- Boolean: `all`, `any`, `not`. Depth and node count are capped.
- Validation rejects unknown indicators, out-of-range parameters and empty
  rule trees before a job is created.
- The JSON is canonicalised and hashed; the hash is the run's identity.
- No string code generation and no `eval`.

### 8.3 Execution

- One `RuleStrategy` Nautilus class interprets the validated JSON at runtime.
- Orders are market orders at the close of the signal bar, filled at the next
  bar's open by default; the fill convention is configurable and recorded.
- Costs are explicit. A zero-cost run is allowed but labelled "frictionless".
- Position sizing: fixed stake, percent of bankroll, or fractional Kelly with a
  hard cap. Kelly is computed from the run's own probabilities, never assumed.
- One position per instrument; no pyramiding in v1.

## 9. Backtest lifecycle

1. `POST /api/backtests` validates the request and writes a `queued` row.
2. A worker subprocess builds/loads the catalog slice, runs
   `BacktestNode`, and writes artifacts.
3. The API exposes status and progress over SSE; the UI shows queued →
   running → complete/failed.
4. Artifacts per run: `config.json`, `summary.json`, `equity.parquet`,
   `trades.parquet`, `positions.parquet`, `log.txt`, `manifest.json`.
5. Cancellation sends SIGTERM; orphaned workers are reaped on startup.
6. Concurrency is capped (default 1) because Nautilus engines are not
   thread-safe; isolation is by process, not thread.

## 10. API surface (v1)

```
GET  /api/health
GET  /api/meta                       # data versions, catalog coverage
GET  /api/instruments                # filter: venue, league, date
GET  /api/instruments/{id}
GET  /api/bars                       # instrument, bar_type, from, to, limit
GET  /api/indicators                 # registry
POST /api/indicators/preview         # spec + range -> series
GET  /api/strategies                 # built-ins + saved rules
POST /api/strategies                 # validate/save a rule
POST /api/backtests
GET  /api/backtests
GET  /api/backtests/{id}
GET  /api/backtests/{id}/events      # SSE
GET  /api/backtests/{id}/artifacts/{name}
DELETE /api/backtests/{id}
```

All responses are JSON with explicit units and UTC timestamps. Errors use a
stable `{code, message, details}` shape.

## 11. UI

Pages:

- **Data explorer** — venue, league, market, selection, coverage, gaps.
- **Chart** — candles, volume, overlays and separate panes, indicator editor,
  URL-encoded state.
- **Strategy** — built-in picker or rule builder, live validation, cost and
  sizing panel.
- **Results** — metrics, equity, drawdown, trade list, markers on the chart.
- **Runs** — history with config hash, data version, status.

Rules:

- The chart never computes indicator math; it renders server series.
- Every number has a unit and a source (data version + timestamp).
- Empty and missing-data states are explicit; no fabricated candles.
- The palette is colour-blind safe; candles remain distinguishable in dark and
  light themes.
- Keyboard navigation for the chart range and indicator panel.

## 12. Architecture

```
Browser (React + TS + Vite, lightweight-charts)
        │  HTTPS over tailnet
        ▼
FastAPI (127.0.0.1:8787) ── serves built frontend at /
        │
        ├── DuckDB read model ── var/bars/**/*.parquet
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

- canonical strategy JSON + SHA-256;
- data version and the exact source paths;
- bar type and aggregation chain;
- fee/slippage/fill assumptions;
- code version (git SHA) and NautilusTrader version;
- artifact hashes.

The UI shows the data version and code version on the results page. A run whose
data version is no longer present is marked `orphaned`, not silently re-run.

## 15. Performance targets

- Chart query: p95 < 300 ms for 20k bars from a warm DuckDB.
- Indicator preview: p95 < 500 ms for 20k bars and 8 indicators.
- Backtest: 1 instrument, 1 year of 1m bars, < 30 s.
- UI: first meaningful paint < 1.5 s on the tailnet.

## 16. Milestones

0. **Scaffold** — repo, AGENTS.md, trail, CI-less test runner, data register.
1. **Data** — canonical bar builder for Polymarket/Kalshi, data versioning,
   catalog writer, coverage endpoint.
2. **Chart** — instruments, bars, candles, volume, range controls, URL state.
3. **Indicators** — registry, Nautilus adapter, cache, chart overlays/panes,
   parity fixtures.
4. **Backtests** — rule schema, validator, `RuleStrategy`, worker, artifacts,
   results page.
5. **Built-ins** — the ten strategies above with provenance and golden runs.
6. **Hardening** — Tailscale serve unit, cancellation, orphan cleanup,
   acceptance suite.

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

## 18. Risks

| Risk | Mitigation |
|---|---|
| Prediction-market prices are not a continuous tradable series | Model tokens explicitly; no bar when no trade; settlement at expiry |
| Polymarket/Kalshi bar semantics differ | One canonical schema with `source` and per-venue builder tests |
| Chart and backtest indicator drift | Single Python implementation + parity fixtures |
| Nautilus 2.x is a pre-release (rc9) | Pin the exact version; adapter boundary isolates upgrades |
| Long backtests block the API | Process isolation, queue, SSE, cancellation |
| Data licences forbid redistribution | Data stays outside the repo; read-only; register records terms |

## 19. Open decisions

- Whether Betfair is normalised to probability for charting before its v2
  adapter lands.
- Whether v2 portfolio backtests share one bankroll or run sleeve-per-instrument.
- Whether saved strategies are per-user files or SQLite rows (default: SQLite).
