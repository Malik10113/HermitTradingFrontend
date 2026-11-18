# Session master — 2026-11-18 — v1

> The wrapup for the v1 build. `docs/BRIEF.md` stays authoritative for design;
> this file records what shipped, what was deliberately left out, the two
> corrections that matter (USD, not GBP; rc9 replay mechanics), and the
> commands that evidence acceptance. It is documentation only: no code or test
> claims here are made on behalf of a session that did not run them.

## Goal

Ship the v1 research instrument described in the brief: read-only Polymarket
and ESPN data, OHLC bars, server-computed Nautilus indicators, declarative
strategies, deterministic backtests with five execution profiles, a process
queue, results artifacts, and a tailnet-only React workspace. Then reconcile
the implementation notes, changelog and data register with the code.

## What shipped

| Area | What exists now | Entry points |
|---|---|---|
| Data versions | SHA-256 over ordered file fingerprints plus builder version; cached in `var/data_versions.json` | [`backend/data/versions.py`](../backend/data/versions.py) |
| Ingestion | Read-only Polymarket tape/markets/results/books and ESPN cache; no writes to the sibling tree | [`backend/data/sources.py`](../backend/data/sources.py), [`backend/data/instruments.py`](../backend/data/instruments.py) |
| Bars | 1-minute fills → 5m/15m/1h/1d composite bars; tick/volume/value/Renko bars; canonical schema; no bar for an empty bucket | [`backend/data/bars.py`](../backend/data/bars.py), [`scripts/build_bars.py`](../scripts/build_bars.py) |
| Lifecycle | ESPN `keyEvents` kickoff/end wallclock → `var/lifecycle/match_lifecycle.parquet`; missing row means `unknown` | [`scripts/build_match_lifecycle.py`](../scripts/build_match_lifecycle.py), [`backend/data/lifecycle.py`](../backend/data/lifecycle.py) |
| Settlement | 0/1 values indexed from the results table; value never inferred from a price | [`backend/data/settlement.py`](../backend/data/settlement.py) |
| Quality | Coverage, gaps, trade count, active minutes, grade and warnings for the preflight | [`backend/data/quality.py`](../backend/data/quality.py) |
| Indicators | Ten v1 specs (SMA, EMA, WMA, RSI, MACD, Bollinger, ATR, Stochastic, Donchian, VWAP) computed by Python/Nautilus for chart and backtest | [`backend/indicators/registry.py`](../backend/indicators/registry.py), [`backend/indicators/compute.py`](../backend/indicators/compute.py) |
| Rules | Pydantic schema, capped boolean tree, canonical strategy/run hashes, runtime evaluator | [`backend/rules/schema.py`](../backend/rules/schema.py), [`backend/engine/rule_strategy.py`](../backend/engine/rule_strategy.py) |
| Built-ins | Ten curated, provenance-tagged strategies that compile to the same rule execution path | [`backend/strategies/builtins.py`](../backend/strategies/builtins.py) |
| Execution | Frictionless, Optimistic, Conservative, Stress, Book; per-fill Conservative fallback; book walk with partial depth | [`backend/engine/execution.py`](../backend/engine/execution.py) |
| Runs | Process-isolated queue, SSE events, cancellation, orphan reap, deterministic artifacts and manifest | [`backend/jobs/queue.py`](../backend/jobs/queue.py), [`backend/engine/runner.py`](../backend/engine/runner.py), [`backend/engine/artifacts.py`](../backend/engine/artifacts.py) |
| API | Instruments, bars, indicators, preflight, strategies, presets, runs, compare, experiments, cache stats | [`backend/api/routes.py`](../backend/api/routes.py) |
| Workspace | Chart, indicator chips, strategy rail, results drawer, compare, data/runs/presets pages | [`frontend/src/pages/WorkspacePage.tsx`](../frontend/src/pages/WorkspacePage.tsx) |
| URL state | Instrument, bar type, range, indicators, strategy, profile, run id, compare ids and phase; reset restores remembered defaults | [`frontend/src/lib/urlState.ts`](../frontend/src/lib/urlState.ts) |
| Experiments | Parent with retained child runs, one or two API axes, bounded grid, cancel parent | [`backend/api/routes.py`](../backend/api/routes.py), [`frontend/src/components/results/SweepsPanel.tsx`](../frontend/src/components/results/SweepsPanel.tsx) |
| Holdout | Out-of-sample metrics hidden until reveal; append-only ledger; second reveal warns; variant counter | [`backend/api/routes.py`](../backend/api/routes.py), [`backend/jobs/state.py`](../backend/jobs/state.py) |
| Deploy | Loopback unit, Tailscale Serve script, operations runbook | [`deploy/hermit-frontend.service`](../deploy/hermit-frontend.service), [`docs/OPERATIONS.md`](OPERATIONS.md) |

## Deliberately deferred

| Item | Why it is out of v1 | Where the boundary is recorded |
|---|---|---|
| Kalshi and Betfair | One venue keeps bar semantics, token mapping and settlement unambiguous. Betfair is decimal odds, not 0–1 probabilities. | `docs/BRIEF.md` §3, §4 |
| Tick-by-tick L2 replay | v1 Book uses the latest snapshot at or before the decision time. Snapshots have no queue position; they are not a replay engine. Pre-2026 has no books at all. | `docs/DATA_TRACE.md`, `docs/IMPLEMENTATION.md` |
| Fractional Kelly | Needs a calibrated probability; v1 ships fixed stake and risk % only. | `docs/BRIEF.md` §8.3 |
| Portfolio / multi-instrument runs | One instrument (with its YES/NO pair) per run; portfolio backtests are v2. | `docs/BRIEF.md` §3 |
| Range bars | Nautilus 2.x has no range aggregation. | `docs/BRIEF.md` §6 |
| Arbitrary uploaded Python | Declarative rules only; no `eval`, no uploaded code. | `AGENTS.md` §2 |

## The USD-not-£ correction

The Polymarket tape stores `usd_amount`; there is no verified GBP conversion
anywhere in the acquired data. v1 therefore sizes, charges and reports in
**USD**:

- `stake` and bankroll are USD; the starting account balance is
  `Currency("USD")`.
- API fields are explicit: `_usd` for money, `_bps` for costs.
- `bankroll_gbp` is accepted only as a legacy input alias and normalised to
  `bankroll_usd` in [`backend/rules/schema.py`](../backend/rules/schema.py).
- The UI labels stake, bankroll and P&L in USD.

This corrects the £ wording in the brief rather than inventing an exchange
rate. Do not add an FX assumption to make old labels true.

## Nautilus 2.0 rc9 gotchas (verified)

1. **Size precision is load-bearing.** Every quantity supplied as data or an
   order must match the instrument's size precision. Use
   `instrument.make_qty(...)`. A raw `Quantity.from_int` on a
   `size_precision=2` instrument is silently dropped by the matching engine
   (upstream issue #5461); the strategy sees bars but never fills.
2. **Composite bars are stored INTERNAL but replayed EXTERNAL.** The canonical
   data artifact is `...-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL` (rc9
   rendering; the docs form with an inner `-LAST` is accepted on input and
   normalised). Precomputed bars must be handed to Nautilus with
   `AggregationSource.EXTERNAL`, so the runner re-types them at replay time.
   Only `1m` sets `bar_execution=True`; composite and non-time bars are
   delivered to the strategy but do not drive matching.
3. **Next-open fills need a quote, not an `on_bar` market order.** A market
   order submitted from `on_bar` fills at that bar's close. To get the
   configured next-bar-open convention, the runner emits one zero-spread
   `QuoteTick` per bar at the bar's open (`ts_init = open + 1 ns`), queues the
   intent in `on_bar`, and submits it in `on_quote`. Bars are added before the
   open quotes so the signal bar closes first at a shared timestamp.
4. **The fill model must never raise.** An exception in
   `FillModel.get_orderbook_for_fill_simulation` aborts the fill silently.
   Every lookup is guarded; a missing/stale book snapshot falls back per fill
   to Conservative.
5. **Sells are `reduce_only`.** A CASH account rejects an unqualified sell as a
   naked short. An exit sells exactly the held quantity.

## Acceptance checklist

The left column restates the v1 acceptance criteria. The command is the
evidence to run; this docs-only session did not re-run the suites, so no
"passed" claim is implied by the table.

| # | Criterion | Evidence command |
|---:|---|---|
| 1 | Cold start lists registered markets and coverage | `curl -s localhost:8787/api/meta`; `curl -s 'localhost:8787/api/instruments?limit=5'` |
| 2 | Candles match the stored bars bar-for-bar | `uv run pytest tests/data/test_bars.py tests/data/test_readmodel.py`; compare `/api/bars` with `read_parquet` on the same `var/bars/...` file |
| 3 | Chart and backtest use the same indicator values | `uv run pytest tests/indicators/test_parity.py tests/indicators/test_expected_fixtures.py` |
| 4 | A saved rule reloads from its URL with the same run hash | `cd frontend && npm test -- --run src/lib/urlState.test.ts`; `curl -s 'localhost:8787/api/backtests/<run_id>' \| jq .run_hash` |
| 5 | Equity, drawdown, trades, markers trace to artifacts | `ls var/runs/<run_id>/` (ten artifacts); `curl -s 'localhost:8787/api/backtests/<run_id>/artifacts/equity.parquet?format=json&limit=5'` |
| 6 | Tailnet URL only; no public listener | `ss -ltnp \| grep 8787`; `tailscale serve status` |
| 7 | Killing the server leaves no orphan worker | `uv run pytest tests/api/test_state.py -k orphan`; manual `systemctl restart hermit-frontend` then inspect `var/app.db` |
| 8 | Frictionless reports zero fees and slippage | `uv run pytest tests/engine/test_execution.py tests/engine/test_analytics.py -k 'frictionless or costs'` |
| 9 | Conservative charges fee + `max(price×bps, min_abs)` | `uv run pytest tests/engine/test_execution.py -k 'formula or conservative'` |
| 10 | Book on pre-2026 data resolves Conservative + reason | `uv run pytest tests/engine/test_execution.py -k book_without_coverage` |
| 11 | Stale/missing snapshot falls back per fill only | `uv run pytest tests/engine/test_execution.py -k 'stale or missing_snapshot'` |
| 12 | No future book snapshot; partial depth leaves remainder unfilled | `uv run pytest tests/engine/test_execution.py -k 'future_snapshot or partial'` |
| 13 | Close long sells the token; open short buys NO | `uv run pytest tests/engine/test_engine_integration.py -k 'round_trip'` |
| 14 | Missing lifecycle row yields `unknown`, never a nominal duration | `uv run pytest tests/data/test_lifecycle.py -k unknown` |
| 15 | Holdout ledger warns on second reveal; variants counted | `uv run pytest tests/api/test_state.py -k 'reveal or variant'` |
| 16 | Two or three runs compare with profile costs | `cd frontend && npm test -- --run src/components/results/ResultsDrawer.test.tsx`; `curl -s 'localhost:8787/api/runs/compare?ids=<a>,<b>'` |
| 17 | Preflight warnings; acknowledged warnings reach the manifest | `curl -s 'localhost:8787/api/instruments/<id>/quality'`; `jq .acknowledged_warnings var/runs/<run_id>/manifest.json` |
| 18 | Two-parameter sweep and cancel parent | API: `POST /api/experiments` with two axes; `uv run pytest tests/api`; **UI exposes one axis and no heatmap — partial** |
| 19 | Signals are reused across execution profiles | **Not implemented as a signal cache; the strategy hash is unchanged but signals are recomputed per run — partial** |
| 20 | URL restores the full workspace | `cd frontend && npm test -- --run src/lib/urlState.test.ts src/pages/WorkspacePage.test.tsx` |

Full local gate before merge:

```bash
scripts/test_domains.sh --all
cd frontend && npm test -- --run && cd ..
uv run pytest tests --quiet
uv run python scripts/data_register.py verify
```

## Known gaps to carry into v1.1

- The UI sweep is one-axis and tabular; the API accepts two axes, but no
  heatmap component exists. Acceptance criterion 18 is partial.
- There is no signal cache. `docs/BRIEF.md` §8.6 and acceptance criterion 19
  describe one; the code recomputes indicator/signal state per run.
- `GET /api/meta` names the markets dataset as
  `results/polymarket_markets.parquet`; the reader actually uses
  `polymarket/sii/markets.parquet`. The register is correct; the meta label is
  not.
- `AGENTS.md` and `docs/BRIEF.md` show the composite bar with an inner
  `-1-MINUTE-LAST-EXTERNAL`; rc9 renders it without the inner `-LAST`. The
  parser accepts both, and `docs/IMPLEMENTATION.md` records the canonical form.
- The lifecycle register entry still records `size_bytes: 0`; a built lifecycle
  file verifies as `SIZE_DRIFT` until it is re-measured after a build.

## Next step

Close the two partial acceptance criteria: add a signal cache keyed by
`(data_version, bar_type, strategy_hash, warmup, indicator_impl_version)` and a
second axis + heatmap in the sweep UI. Then re-run `scripts/test_domains.sh
--all` and re-check the acceptance table.
