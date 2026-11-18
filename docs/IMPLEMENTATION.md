# Implementation notes (v1)

This is the working implementation contract. `docs/BRIEF.md` remains
authoritative for design; this file records how the code satisfies it, the
data semantics the code relies on, and the interfaces between packages.

## Environment

- Python 3.13 via `uv`; dependencies pinned in `pyproject.toml`.
- `nautilus_trader==2.0.0rc9` (the 2.x pre-release named by the brief).
- Node 26 / npm for `frontend/`.
- The sibling data root is read-only:
  `/root/FundedBetBacktesting/data` (override with `HTF_DATA_ROOT`).
- Generated state lives under `var/` (override with `HTF_VAR`).

## Data semantics

### Polymarket tape

`polymarket/{football,epl}/*_moneyline_tape.parquet` is YES-normalised:

- one row per fill, keyed by `condition_id`;
- `condition_id` identifies one binary moneyline leg (home/draw/away), not a
  whole match;
- `price_yes` is the YES-token price; the NO-token price is its complement,
  `1 - price_yes`;
- `usd_amount` is the fill notional in USD; `token_amount` is contracts;
- `side_yes = +1` when the taker ends up long YES;
- `match_key` joins the lifecycle and results spine.

### Markets and tokens

`polymarket/sii/markets.parquet` supplies `condition_id`, `slug`,
`token1` (YES), `token2` (NO), `answer1/answer2`, `end_date` and `outcome_prices`.
One Nautilus `BinaryOption` is created per token:

```
instrument_id = "{market_slug}-YES.POLYMARKET"   # or -NO
```

`market_slug` is unique per leg and readable in the UI. The YES and NO
instruments of a condition are a pair: a long on the primary token buys it, a
short buys the sibling token. Closing always sells the held token.

### Settlement

`results/polymarket_markets.parquet` has one row per `(market_id, selection_index)`,
where `market_id` is the `condition_id` and `selection_index = 0` is YES.
`settlement_value` is 0 or 1. We index it into
`var/settlement/settlement.parquet`; when a row is missing the value is unknown
and a run refuses to settle that instrument (the UI says so).

`markets.parquet.end_date` is the metadata cutoff used as the instrument
expiration. It is not a verified settlement timestamp; we do not invent one.
Closed markets carry `updated_at`, which is recorded as `settled_ts` when
present.

### Lifecycle

`scripts/build_match_lifecycle.py` reads the cached ESPN summaries
(`data/events/espn/summary/{league}_{event_id}.json.gz`) through
`data/events/fixture_map.parquet` and writes
`var/lifecycle/match_lifecycle.parquet` with:

```
match_key, actual_kickoff_utc, actual_end_utc, source, precision
```

`source = espn_keyevents`, `precision = observed`. Rows without a `wallclock`
are omitted. `backend/data/lifecycle.py` computes phases from that file only;
a missing row yields `phase = unknown` and no nominal duration.

### Bars

Canonical fields: `instrument_id, bar_type, ts_event, ts_init, open, high,
low, close, volume, trade_count, source, data_version`.

- 1-minute bars are built from fills. `open/high/low/close` are last-trade
  prices in time order (`timestamp`, `log_index`); `volume` is USD notional;
  `trade_count` counts fills.
- A token's bars use its own price: YES uses `price_yes`, NO uses
  `1 - price_yes`.
- `ts_event` is the UTC start of the minute bucket; `ts_init` is the bucket
  close (`ts_event + 60s`), which Nautilus bar execution requires.
- Empty minutes produce no bar.
- Higher timeframes are aggregated from 1m bars; a bucket with no 1m bars
  produces no bar.
- Bar types: `...-1-MINUTE-LAST-EXTERNAL`,
  `...-5-MINUTE-LAST-INTERNAL@1-MINUTE-LAST-EXTERNAL`, and the same for
  15m/1h/1d.
- Non-time bars (`tick`, `volume`, `value`, `renko`) are built from the fill
  stream and use the same canonical schema. A `tick` bar closes every N fills,
  `volume` every N contracts (`token_amount`), `value` every N USD, and `renko`
  every brick of price movement (default 0.01). They are not supported by
  Nautilus bar execution; a run that uses one sets `bar_execution=False` and
  fills at the next bar's open quote.

## Nautilus 2.0 notes (verified)

- `BinaryOption` works in the matching engine, but **every quantity passed as
  data or an order must use the instrument's size precision**. Use
  `instrument.make_qty(...)`; a raw `Quantity.from_int` is silently dropped by
  the matching engine (upstream issue #5461).
- Bar execution requires external, time-aggregated bars with
  `ts_init = bar close`.
- A market order submitted from `on_bar` fills at that bar's close. To get the
  configured **next-bar-open** convention we also add one zero-spread
  `QuoteTick` per bar at the bar's open, with `ts_event = open` and
  `ts_init = open + 1 ns`. The strategy queues the intent in `on_bar` (close of
  bar N) and submits it in `on_quote` (open of bar N+1). The +1 ns places the
  open quote after the bar that closed at the same timestamp, so the signal
  formed on bar N is the one that fills at the next open; quotes are added
  before bars for stable replay order.
- Execution profiles are implemented with a `FillModel` that returns a
  synthetic L1 `OrderBook` at the profiled fill price, and a `FeeModel` that
  charges `fee_bps` of notional. The fill model receives the order and can read
  `order.side` and `order.ts_last`.
- A sell must pass `reduce_only=True`; a CASH account otherwise rejects it as
  a naked short. An exit sells exactly the held quantity, not a freshly sized
  stake.
- Settlement is applied by the analytics layer as an explicit cash flow for
  positions still open at expiry, using the indexed settlement value. It is
  never inferred from a price.
- `FillModel.get_orderbook_for_fill_simulation` must not raise: an exception
  aborts the fill. Guard all lookups.

## Execution profiles

Resolved once per run and recorded in the manifest:

| Profile | Cost source | Fee | Slippage | Minimum absolute |
|---|---|---|---|---|
| Frictionless | `none` | 0 | 0 | 0 |
| Optimistic | `fixed` | venue | 0 | 0 |
| Conservative | `fixed` | venue | 10 bps | 0.001 |
| Stress | `fixed` | venue | 25 bps | 0.002 |
| Book | `dynamic` | venue | book walk | — |

`slip = max(c * slippage_bps / 10_000, min_abs_slippage)`, buy at `c + slip`,
sell at `c - slip`, clamped to `[tick, 1 - tick]`. Book uses the latest L2
snapshot with `ts <= decision_ts`, walks asks for buys and bids for sells up to
`max_depth_levels`, and falls back per fill to Conservative when the snapshot
is missing or older than `book_staleness_s`. A run with no book coverage is
priced as Conservative with `reason = no_book_coverage`; it never silently uses
Frictionless.

## Package layout

```
backend/
  config.py              paths, settings, code version
  data/                  versions, sources, bars, instruments, quality,
                         lifecycle, settlement, read model
  indicators/            registry, Nautilus computation, cache
  rules/                 Pydantic schema, validation, canonical hashes, evaluator
  strategies/            built-in catalogue
  engine/                instruments, execution, RuleStrategy, runner, analytics
  jobs/                  queue, subprocess, SSE, cancellation, orphan reap
  api/                   FastAPI routes and schemas
  main.py                app factory
frontend/src/            React workspace
scripts/                 build bars, catalog, lifecycle, test runner
tests/                   domain tests
var/                     generated (gitignored)
```

## API contract

All routes are under `/api`. Errors are `{code, message, details}` with an
HTTP status. Timestamps are ISO-8601 UTC in JSON; bar timestamps are also
available as `_ns` integers. Sizes/notional are `_usd`; probabilities are
0–1; costs are `_bps`.

```
GET  /api/health
GET  /api/meta
GET  /api/instruments?league=&q=&limit=&offset=
GET  /api/instruments/{instrument_id}
GET  /api/instruments/{instrument_id}/quality?from=&to=
GET  /api/search?q=
GET  /api/bars?instrument_id=&bar_type=&from=&to=&limit=
GET  /api/indicators
POST /api/indicators/preview
POST /api/execution/preflight
GET  /api/strategies
POST /api/strategies
GET  /api/presets  / POST /api/presets
POST /api/backtests
GET  /api/backtests
GET  /api/backtests/{run_id}
GET  /api/backtests/{run_id}/events        (SSE)
GET  /api/backtests/{run_id}/artifacts/{name}
POST /api/backtests/{run_id}/reveal-holdout
GET  /api/runs/compare?ids=
GET  /api/experiments  / POST /api/experiments
GET  /api/experiments/{id}
POST /api/experiments/{id}/cancel
DELETE /api/backtests/{run_id}
```

`bar_type` in the API is a semantic alias (`1m`, `5m`, `15m`, `1h`, `1d`,
`tick_100`, `volume_1000`, `value_10000`, `renko_0.01`) or a full Nautilus bar
type string.

`GET /api/bars` response:

```json
{
  "instrument_id": "epl-bou-ars-2024-10-19-bou-YES.POLYMARKET",
  "bar_type": "...-1-MINUTE-LAST-EXTERNAL",
  "data_version": "sha256:…",
  "bars": [
    {"ts_event": "2024-10-19T10:00:00Z", "ts_event_ns": 1729332000000000000,
     "open": 0.42, "high": 0.44, "low": 0.41, "close": 0.43,
     "volume_usd": 1234.5, "trade_count": 17}
  ],
  "has_more": false
}
```

`POST /api/indicators/preview` body:

```json
{"instrument_id": "...", "bar_type": "1m",
 "from": "2024-10-01", "to": "2024-12-01",
 "indicators": [{"instance_id": "ema20", "type": "ema", "params": {"period": 20}}]}
```

Response: `{"series": {"ema20": {"value": [null, …]}}, "warmup": {"ema20": 19},
"data_version": "…", "bars": 1234}`.

`POST /api/execution/preflight` body is the same `execution` block as a run
plus instrument/date; response carries `requested`, `effective`,
`fallback_reason`, `book_coverage` and a `cost_estimate` (per side, in bps and
price points), never a profitability claim.

`POST /api/backtests` accepts the full config (`strategy`, `execution`,
`risk`, `evaluation`). It returns `{run_id, status, run_hash, strategy_hash}`.
Status is `queued|running|complete|failed|cancelled|orphaned`.

## Testing domains

`scripts/test_domains.sh` maps changed paths to `frontend`, `data`,
`indicators`, `engine`, `api`, `e2e`, `tooling` and runs dependents. Until the
frontend exists, `--domain frontend` reports nothing to run.
