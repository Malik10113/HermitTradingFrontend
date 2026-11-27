# Session master — v1 handoff (2026-11-19)

> Superseded for current state by
> `SESSION_MASTER_2026-11-27_V1_FRONTEND_DATA.md`; kept as history.

> Supersedes `SESSION_MASTER_2026-11-18_V1.md` for current state. The brief
> remains authoritative for design. This file tells the next agent what is
> deployed, what is verified, what is still open, and which worktrees are stale.

## 1. Current state

| Item | Value |
|---|---|
| Build checkout | `/root/HermitTradingFrontend` |
| Production checkout | `/opt/hermit/prod` (detached at `98fb081`; master is one docs-only commit ahead) |
| Production state | `HTF_VAR=/opt/hermit/var` |
| Build/dev state | `HTF_VAR=/opt/hermit/var-dev` |
| Production service | `hermit-frontend.service` (enabled, active) |
| Bind | `127.0.0.1:8787`, tailnet only; no public listener |
| Code version | `98fb0815e3fe55c56abbac99f2158f9193133b92` |
| Data version | `sha256:898cf6ab1b01d2acee425b61dd037c09af04316d686021c7b83e392bb30e12e8` |
| Lifecycle rows | 1,880 total; 874 pre-2026 |
| Frontend tests | 23 files, 145 tests passing |
| Backend tests | full `uv run pytest -q` green |
| Tailscale Serve | **not enabled**; admin console enable required (see §5) |

Master is clean and pushed (`6e38342`). Production is at `98fb081`; the delta is
docs/handoff only. `deploy/promote.sh` is the only supported way to move
build → production.

## 2. What is implemented

- Read-only Polymarket tape/markets/results/L2 snapshots and ESPN lifecycle
  cache. Generated state only under `var/`.
- 1m fill-built bars, 5m/15m/1h/1d composites, tick/volume/value/Renko bars,
  canonical schema, no empty buckets.
- Ten Nautilus-backed indicators with registry metadata, warm-up nulls, golden
  parity fixtures, and an LRU indicator cache.
- Declarative rule schema with canonical strategy/run hashes, capped trees,
  ten built-in strategies with provenance.
- Five execution profiles, synthetic fixed-price fill model, real L2 Book walk
  with per-fill Conservative fallback, stale-snapshot and leakage guards.
- Single-instrument runs in isolated worker processes; cancellation, orphan
  reaping, SSE, SQLite state, holdout ledger, experiments/sweeps, compare.
- Persistent two-tier signal cache (`var/cache/signals/`, 128 plans / 256 MiB),
  keyed by data version, bar type, strategy hash, warm-up and indicator impl
  version. Cross-worker reuse is proven by tests.
- Lifecycle backfill from ESPN scoreboards/summaries, resumable cache under
  `var/raw/espn/`, concurrency and `--stage`.
- Run artifacts include config/summary/equity/trades/positions/signals/costs/
  diagnostics/log/manifest; run detail exposes per-leg execution provenance,
  entry context, position windows and open positions.
- API contract: filters, paging, status, stable errors, true totals, gross/net
  semantics, corrected `/api/meta`.
- React workspace, data/runs/presets pages, URL state, chart markers/phase
  shading, indicator chips, strategy rail, results drawer, trade inspector,
  accessibility, Playwright acceptance suite.

## 3. Data coverage and backfill

Pre-2026 spine (`results/polymarket_events.parquet`, read-only): 948 fixtures.
Current lifecycle coverage: 874 of 948 (92%). The remaining 74 are exactly:

- 21 `unmatched`: no unique ESPN event for the fixture date/team names.
- 53 `incomplete`: ESPN summary had no full kickoff/end-regular-time wallclock pair.

No row was guessed. Fixtures without a row correctly resolve to `phase = unknown`.
Backfill logs:

- `/tmp/backfill_epl.log` — EPL 2024-10→12: 91 rows.
- `/tmp/backfill_epl_2025.log` — EPL 2025: 349 rows.
- `/tmp/backfill_remaining.log` — Big-4 2025-08→12: 434 rows.

The next useful data step is auditing the 21 unmatched fixtures; only add a row
if a deterministic ESPN event exists. Do not fuzzy-match to raise coverage.

## 4. Deploy / build workflow

Build and test in `/root/HermitTradingFrontend`. Production is untouched.

```bash
cd /root/HermitTradingFrontend
scripts/test_domains.sh --all
./deploy/promote.sh                # origin/master
./deploy/promote.sh <commit>       # rollback or pin
```

`promote.sh` fetches, checks out the target in `/opt/hermit/prod`,
`uv sync --frozen`, rebuilds `frontend/dist`, restarts the unit and health-checks
`/api/health`. Production `var/` is outside the checkout and is never overwritten.

Optional build preview on 8788 with its own state:

```bash
sudo install -m 0644 deploy/hermit-frontend-dev.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermit-frontend-dev
```

## 5. Connecting

Local: `http://127.0.0.1:8787`.

Tailnet: Serve must first be enabled for the node. The CLI printed:

```
https://login.tailscale.com/f/serve?node=nrVw2dVL3K11CNTRL
```

Open that from a browser, enable Serve, then on the server:

```bash
tailscale serve --bg 8787
tailscale serve status
```

URL: `https://vmi3689947.tail8d840b.ts.net`.

Never run `tailscale funnel`. Verify `ss -ltnp | grep 8787` shows only
`127.0.0.1:8787`.

## 6. Remaining work

Not started / deferred:

1. **Data register** — `results/polymarket_events.parquet` is read by
   `backend/data/instruments.py` but is not registered. `espn_match_lifecycle`
   still records size 0 while the file is ~30 KB. Update the register and
   re-render after the data set is stable.
2. **Data-quality gap list** — API/UI expose coverage, active minutes, longest
   gap and grade, but not the brief’s list of gaps. Add it from stored trade
   timestamps; never invent gaps.
3. **`HTF_APP_TOKEN` browser flow** — backend enforcement is complete. The
   browser still has no reviewed way to send `Authorization: Bearer`; do not
   embed the token in the Vite bundle.
4. **Preset parameter reproducibility** — verify whether opening a builtin
   preset restores `builtin_params` and risk/execution overrides; verification
   fixed quick params/preset sizing but this needs a focused test.
5. **Nautilus import boundary** — `backend/data/bars.py`,
   `backend/indicators/compute.py` and `backend/config.py` import
   `nautilus_trader`, which conflicts with AGENTS §3 but is required by §6.
   Resolve as a documented rule clarification before changing code.
6. **Unmatched lifecycle fixtures** — audit the 21; do not lower matching rigor.
7. **Production hardening** — add a restart/rollback smoke test for
   `deploy/promote.sh`, and run the Playwright suite against production after a
   promotion.

## 7. Known traps (learned)

- **Size precision:** use `instrument.make_qty()`; raw `Quantity.from_int` on a
  `size_precision=2` instrument is silently dropped and no order fills.
- **Next-open fills:** `on_bar` market orders fill at that bar’s close. Emit a
  quote at the next bar’s open (`ts_init = open + 1 ns`) and submit from
  `on_quote`; bars are added before quotes.
- **Composite bars:** stored `...-INTERNAL@...-EXTERNAL`; replay to Nautilus as
  EXTERNAL, and only 1m drives `bar_execution=True`.
- **Sells:** pass `reduce_only=True`; CASH accounts reject unqualified sells as
  naked shorts. Exit quantity is the held quantity, not a new stake.
- **Fill model:** never raise inside `get_orderbook_for_fill_simulation`; guard
  every lookup and fall back to Conservative.
- **Cache keys:** execution/sizing/evaluation must never enter the signal key.
  The disk store is required because each API run is its own worker process.
- **Data versions:** paths are repo/VAR-relative. Moving a checkout no longer
  orphans runs; changing the lifecycle file does.
- **USD, not GBP:** the tape is USD notional; no FX conversion is invented. The
  legacy `bankroll_gbp` input is accepted only as an alias.

## 8. Worktrees / branches

All feature branches below are already merged into master; their worktrees are
now stale and should be reset to master or removed before more work:

`feature/experiments`, `feature/hardening`, `feature/ux`, `feature/lifecycle-backfill`,
`feature/signal-cache`, `feature/run-provenance`, `feature/api-filters`,
`docs/v1`, `verify/v1-fixes`.

Do not merge those branch tips again. For a new task, create a fresh worktree
from current master:

```bash
git worktree add /tmp/opencode/<name> -b feature/<name> origin/master
```

## 9. Commands

```bash
# full gate
scripts/test_domains.sh --all
cd frontend && npm test -- --run && npm run build && cd ..

# optional E2E (system Chrome, built app)
HTF_E2E=1 HTF_E2E_BASE_URL=http://127.0.0.1:8787 scripts/test_domains.sh --e2e  # see runner

# data checks
uv run python scripts/data_register.py verify
uv run python scripts/build_match_lifecycle.py            # offline rebuild
uv run python scripts/build_match_lifecycle.py --backfill --from ... --to ... --leagues ... --stage

# signals cache
curl -s localhost:8787/api/cache/stats | jq
```

## 10. First ten minutes for the next agent

1. `git status` and `git log -1`; confirm master `98fb081` or later.
2. Read `AGENTS.md`, `docs/BRIEF.md`, this file, and
   `.trail/learnings/2026-11-19-*`.
3. Run `scripts/test_domains.sh --all`.
4. `curl -s localhost:8787/api/health` and `systemctl status hermit-frontend`.
5. Confirm production path/var before touching deployment.
6. Pick one item from §6; create a fresh worktree; commit and push a topic
   branch; do not push master until reviewed and green.
