# Changelog

Newest first. One entry per discrete change: what changed, why, and what was
measured. Keep entries short enough to scan; link the commit or PR.

## Template

```markdown
## YYYY-MM-DD  <short title>

- **Changed:** <what the diff does, in one or two lines>
- **Why:** <the reason; link the issue, learning or decision>
- **Measured:** <what was run, and the result; "not measured" is a valid answer>
- **Notes:** <anything a reviewer would otherwise have to reconstruct>
```

---

## 2026-11-18  v1: data layer, versions and bar builders

- **Changed:** Added read-only source adapters, content-derived data versions,
  the 1-minute fill→OHLCV builder, 5m/15m/1h/1d aggregation and tick/volume/
  value/Renko bars. Added the ESPN lifecycle builder, settlement index,
  instrument catalogue/quality and the bar cache.
- **Why:** every chart value and backtest needs one traceable series with no
  fabricated bars and a stable data version (`docs/BRIEF.md` §4, §6).
- **Measured:** `uv run pytest tests/data` covers bucketing, YES/NO
  complementing, alias parsing, no empty buckets, half-open ranges, cache
  metadata and lifecycle omissions. Performance targets were not measured.
- **Notes:** canonical `data_version` is SHA-256 over ordered
  `(path, size, mtime_ns, rows, min_ts, max_ts)` plus the builder version. A
  missing lifecycle row is `unknown`; it is never replaced by a nominal
  duration. `c05be6b`, `2e5241a`.

## 2026-11-18  Indicators: registry, Nautilus compute, cache and parity

- **Changed:** Added the ten v1 indicator specs, a single Python/Nautilus
  computation path used by chart and backtest, a thread-safe LRU series cache,
  and parity/expected-value fixtures.
- **Why:** one implementation prevents a JavaScript chart series from drifting
  away from the backtest series (`docs/BRIEF.md` §7).
- **Measured:** `uv run pytest tests/indicators` exercises every indicator
  against a golden fixture plus cache-key and cache-stats behaviour. Latency
  targets were not measured.
- **Notes:** warm-up returns `null`, never `0`. The cache key is
  `(data_version, bar_type, spec_hash, indicator_impl_version)`; a parameter
  change is a new entry. `69dfaad`.

## 2026-11-18  Engine: BinaryOption pair, five execution profiles, rc9 adapter

- **Changed:** Added the YES/NO `BinaryOption` pair, rule evaluator, built-in
  catalogue, five execution profiles, book-walk fill model, fee model, next-bar
  open replay and analytics/artifacts. Added composite and non-time bar replay.
- **Why:** the backtest must be deterministic, token-faithful and explicit
  about costs (`docs/BRIEF.md` §5, §8.3–8.5).
- **Measured:** `uv run pytest tests/engine` covers the profile table, the fill
  formula and clamps, book walks/partial depth/future-snapshot rejection,
  Conservative fallback, long/short round trips, fees and settlement. Runtime
  was not measured.
- **Notes:** rc9 silently drops quantities with the wrong size precision; all
  quantities use `instrument.make_qty`. Composite/non-time bars are replayed as
  EXTERNAL; only 1m sets `bar_execution=True`. Next-open fills use one
  zero-spread quote per bar. `ac2c273`, `d18c71f`, `eaa789f`.

## 2026-11-18  API and queue: process isolation, SSE, cancellation, orphans

- **Changed:** Added the `/api` surface, SQLite run/preset/strategy state,
  process-per-run queue, SSE progress, SIGTERM cancellation, startup orphan
  reap and error shape. Added cache stats and preset deletion.
- **Why:** Nautilus engines are not thread-safe and a stale worker must not
  block the dashboard (`docs/BRIEF.md` §9).
- **Measured:** `uv run pytest tests/api` covers health/meta, error shapes,
  run lifecycle, reveal ledger, presets/strategies and orphan termination. Load
  and concurrency were not measured; concurrency is 1.
- **Notes:** a completed run whose data version no longer matches is reported
  as `orphaned`, not re-run. `ac2c273`, `e0b6969`.

## 2026-11-18  Workspace: chart, URL state and shared run links

- **Changed:** Added the React workspace (instrument search, candles, indicator
  chips, strategy rail, results drawer) and URL/default state for instrument,
  bar type, range, indicators, strategy, profile, run id and comparison ids.
  Fixed first-paint handling of shared run URLs.
- **Why:** a view must be shareable and reproducible, and the chart must render
  server-computed series only (`docs/BRIEF.md` §11, `AGENTS.md` §9).
- **Measured:** `npm test -- --run src/lib/urlState.test.ts
  src/pages/WorkspacePage.test.tsx` covers round-trip, invalid-value fallbacks
  and rendering without fabricated data. Browser performance was not measured.
- **Notes:** reset returns remembered defaults but never restores a run id or
  comparison. `c05be6b`, `628c7b3`, `379557d`.

## 2026-11-18  Experiments and bounded sweeps

- **Changed:** Added experiment parent/child runs, one- or two-axis grids with a
  default cap of 50 and a hard cap of 200, retained variants and parent
  cancellation. Added a one-axis sweep panel with progress.
- **Why:** parameter sweeps must be bounded, inspectable and stoppable
  (`docs/BRIEF.md` §16 milestone 4).
- **Measured:** `uv run pytest tests/api` covers experiment state and child
  cancellation. No heatmap or two-axis UI test exists; the UI is one-axis.
- **Notes:** the API accepts two axes, but `SweepsPanel` exposes one axis and a
  table, not a heatmap. Acceptance criterion 18 is therefore partial.
  `3f4672a`.

## 2026-11-18  Holdout hiding and the reveal ledger

- **Changed:** Hid out-of-sample and overall metrics until an explicit reveal;
  added the append-only ledger keyed by `(universe, strategy_hash, split_hash)`,
  a second-reveal warning and a variant counter.
- **Why:** repeated peeking contaminates the test window (`docs/BRIEF.md` §8.5).
- **Measured:** `uv run pytest tests/api/test_state.py -k 'reveal or variant'`
  covers the second-reveal warning and strategy-hash variant indexing.
- **Notes:** execution/sizing changes do not reset the count. Unrevealed
  responses report the in-sample slice with `oos_hidden: true`. `3f4672a`.

## 2026-11-18  Correct currency to USD, not GBP

- **Changed:** Sized and reported stake, bankroll and P&L in USD; renamed the
  label-bearing fields to `_usd`; kept `bankroll_gbp` only as an input alias.
- **Why:** the tape stores `usd_amount` and no verified GBP conversion exists.
  A £ label would be an invented number (`docs/IMPLEMENTATION.md`, trail
  learning "USD, not GBP").
- **Measured:** `uv run pytest tests/engine/test_instruments.py -k currency`
  asserts the instrument currency is USD. No FX conversion was measured or
  added.
- **Notes:** this corrects the £ wording in the brief; do not add an exchange
  rate to make the old label true. `a376302`, `379557d`, `eaa789f`.

## 2026-11-18  Deployment: tailnet unit, Serve script, operations runbook

- **Changed:** Added the checked-in systemd unit, `tailscale-serve.sh`, the
  deployment guide and the operations runbook for start/stop, logs, data-version
  checks, orphan behaviour and `var/` backup.
- **Why:** the dashboard must be reachable only on the tailnet and restart
  without leaving workers behind (`docs/BRIEF.md` §13).
- **Measured:** not measured. The unit and script were not started under
  systemd in this session; `docs/OPERATIONS.md` lists the checks.
- **Notes:** bind `127.0.0.1`, expose with `tailscale serve`; never use Funnel
  and never bind `0.0.0.0`. `e0b6969`.

## 2026-11-18  Record the GitHub remote and push workflow

- **Changed:** `AGENTS.md` names `origin`
  (`Malik10113/HermitTradingFrontend`), makes `master` the mainline, and
  requires pushing each discrete change after tests and `trail check`.
- **Why:** the repo gained a remote; the contract still read as local-only.
- **Measured:** not measured (documentation only).
- **Notes:** `first-pass` tracks the same commit but is not the mainline;
  `var/`, credentials and sibling data stay off the remote.
