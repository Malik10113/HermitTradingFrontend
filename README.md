# HermitTradingFrontend

A single-user, local-first research dashboard for Polymarket football
prediction markets: OHLC candles, server-computed technical indicators and
deterministic NautilusTrader backtests. It is reachable only on a Tailscale
tailnet and never places an order.

The authoritative design is [`docs/BRIEF.md`](docs/BRIEF.md). The implementation
contract is [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md), and the working
rules for agents are [`AGENTS.md`](AGENTS.md).

## What it does

- Builds 1-minute bars from the Polymarket fill tape, plus 5m/15m/1h/1d and
  tick/volume/value/Renko bars. Empty intervals produce no bar.
- Computes every indicator server-side with the pinned NautilusTrader classes;
  the chart and the backtest consume the same series.
- Authors strategies as declarative JSON or from a curated built-in catalogue.
- Runs one instrument per backtest in an isolated worker process, with
  Frictionless, Optimistic, Conservative (default), Stress and Book execution
  profiles. Book falls back to Conservative and records why.
- Stores every run's config, manifest, equity, trades, positions, signals,
  costs, diagnostics and log under `var/runs/<run_id>/`.
- Hides out-of-sample results behind an explicit reveal with an append-only
  ledger.

## Requirements

- Linux, Python 3.13+, [`uv`](https://docs.astral.sh/uv/), Node 26+ with npm.
- The read-only sibling data at `/root/FundedBetBacktesting/data` (override
  with `HTF_DATA_ROOT`). Nothing in this repository writes there.
- Tailscale for remote access; the app binds `127.0.0.1` only.

## Setup

```bash
uv sync
cd frontend && npm install && npm run build && cd ..
uv run python scripts/build_match_lifecycle.py
uv run python scripts/build_bars.py --all --limit 20
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787>. The API is at `/api`; OpenAPI is at
`/api/docs`.

Expose it on the tailnet with the checked-in helper (or the equivalent
`tailscale serve` command):

```bash
./deploy/tailscale-serve.sh
```

Never bind `0.0.0.0`, never enable Funnel, and never add a public URL.

Deployment and day-two operations are in [`docs/DEPLOY.md`](docs/DEPLOY.md) and
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Tests

One entry point:

```bash
scripts/test_domains.sh --changed
scripts/test_domains.sh --domain engine
scripts/test_domains.sh --all
```

See [`TESTING.md`](TESTING.md). Tests never reach the network or the sibling
repo; data-dependent tests use checked-in fixtures or `HTF_INTEGRATION=1`.

## Data and provenance

Only these datasets are read:

| Dataset | Path (under the sibling `data/`) |
|---|---|
| Polymarket EPL tape | `polymarket/epl/epl_moneyline_tape.parquet` |
| Polymarket Big-4 tape | `polymarket/football/football_moneyline_tape.parquet` |
| Polymarket markets | `polymarket/sii/markets.parquet` |
| Polymarket results | `results/polymarket_markets.parquet` |
| ESPN fixture map / summaries | `events/fixture_map.parquet`, `events/espn/summary/` |
| Polymarket L2 books (coverage only in v1) | `cache/orderbooks_compact/` |

`docs/DATA_REGISTER.md` records license, coverage and the rebuild command for
each dataset. Data versions are SHA-256 over ordered file fingerprints plus the
builder version; the same data version, config and code produce the same run
hash.

## Limits

- Polymarket only in v1; Kalshi and Betfair are v2.
- No live trading, no venue credentials, no order placement.
- No L2 replay in v1; the Book profile only uses snapshots where a provider is
  wired and otherwise falls back to Conservative.
- Historical simulation output only. The dashboard makes no profitability
  claim.
