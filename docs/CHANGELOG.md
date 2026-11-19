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

## 2026-11-19  Lifecycle backfill: concurrency, global pacing and staging

- **Changed:** Added `--concurrency` (default 1, cap 8) to the ESPN lifecycle
  backfill, with a shared request-start limiter, in-run URL coalescing, atomic
  cache writes, and `--stage` (`<out>.next` then replace). Cache gzip headers
  and filesystem mtimes are pinned so identical payloads keep the same data
  version.
- **Why:** serial 0.25 s pacing made the remaining pre-2026 Big-4 backfill slow;
  it must be safe to run in the background while the frontend is deployed.
- **Measured:** `uv run pytest tests/data` (63 passed, 1 skipped). Real EPL
  2024-11-01..30 slice: serial 46 requests / 26.69 s; concurrency 4 46 requests /
  24.33 s, both with 36 matched, 28 rows and 8 incomplete. Global delay still
  dominates and scoreboard dates remain serial.
- **Notes:** `--stage` writes `<out>.next` and replaces the real artifact only
  after success. Duplicate summary URLs are coalesced to one request; one failed
  summary records an error and does not abort the run.

## 2026-11-18  Run provenance: per-fill sources, entry context, position windows

- **Changed:** `GET /api/backtests/{id}` trade rows now keep the artifact
  direction (`direction`/`intent`) beside the entry order `side`, carry the
  actual entry/exit `execution_source` and `fallback_reason`, derive per-leg
  `slippage_usd` from reference vs fill prices when known, and attach
  `entry_context` from the nearest signal at or before entry for the same
  instrument and entry action. Run detail also exposes `positions` windows
  (closed/settled) and `open_positions` (unsettled only, no invented exit).
  Zero-trade runs return empty lists. The trade inspector shows entry and exit
  provenance, slippage or "not available", and the condition/indicator snapshot;
  the chart shades only these API windows. Signal artifacts persist the
  indicator snapshot so the inspector has a source for it.
- **Why:** Audit found trade rows reported the entry side as the only side and
  omitted fill-level provenance and entry context because those lived in the
  engine rather than the trade artifact (trail learning
  `l-b0ed23a6`). Acceptance criteria 5, 11 and 13 require traceable numbers,
  per-trade fallback reporting and the actual side/token.
- **Measured:** `uv run pytest tests/api tests/engine -q` and
  `cd frontend && npm test -- --run && npm run build` pass. New API tests cover
  nearest-signal matching, direction preservation, open/settled windows and
  empty artifacts; engine tests cover per-leg slippage and settlement
  provenance.
- **Notes:** Older artifacts without per-leg fields or an indicator snapshot
  show null/not available rather than a reconstructed value; `slippage_usd` is
  null when no reference price was stored.

## 2026-11-18  Signals: persistent plan store so workers share plans

- **Changed:** The signal cache gained a disk tier under `var/cache/signals/`.
  Each plan is one `<signal_spec_hash>.parquet` plus a metadata JSON written
  last with temp-file + `os.replace`; lookups go memory → disk and promote a
  disk hit into the memory LRU. Eviction is LRU by metadata mtime, capped at
  128 plans and 256 MiB. The runner manifest and `log.txt` record
  `hit`/`source` (`memory|disk|none`); `/api/cache/stats` reports memory and
  disk entries/bytes/hits/misses.
- **Why:** API-triggered runs execute in separate worker processes, so the
  in-process LRU could never satisfy acceptance criterion 19 in the real path.
- **Measured:** `uv run pytest tests/engine tests/api -q` and the full suite
  pass. `tests/engine/test_signal_store.py` proves a second, memory-empty cache
  instance and a real subprocess load the persisted plan with the same
  `plan_hash`; a profile-only change hits disk; corrupt/partial files are
  deleted and missed; eviction removes the LRU entry, not the newest.
- **Notes:** lifecycle-version mismatch is a miss but keeps the entry (the
  plan is still valid for its data version); malformed or content-hash
  mismatched entries are deleted. Writes are best-effort: a failed cache write
  never fails a backtest. Cross-process eviction relies on atomic rename and
  the metadata commit marker, not on a cross-process file lock.

## 2026-11-18  Signals: execution-independent plan cache (criterion 19)

- **Changed:** Added `backend/engine/signals.py` — a per-bar `SignalPlan` of
  condition truths plus indicator snapshots, an LRU `SignalCache`, and runner
  integration. `RuleStrategy` consumes a plan when supplied and still owns the
  entry/exit state machine; the runtime-evaluation path is unchanged when no
  plan is given.
- **Why:** re-running one strategy under another execution profile must reuse
  the same signals (`docs/BRIEF.md` §8.6 and acceptance criterion 19); only net
  costs may move.
- **Measured:** `uv run pytest tests/engine tests/api -q` passes, including
  plan/runtime parity over crosses and warm-up nulls, profile-only cache hits,
  miss on every key component, runner manifest hit/miss and identical
  `signals` across a conservative and frictionless run with different net PnL.
- **Notes:** key is `(data_version, bar_type, strategy_hash, warmup_bars,
  indicator_impl_version)`; the instrument is covered by `strategy_hash`.
  `lifecycle_version` is stored and checked, not keyed, because
  `run_data_version()` already fingerprints the lifecycle parquet. Plans are
  built from `bar_to_dict(bars_p)` so indicator and comparison inputs carry the
  same venue price precision as the runtime path. The cache is in-process;
  cross-process reuse would need a persisted plan store.

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
