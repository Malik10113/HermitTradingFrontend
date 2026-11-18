# Review plan — response to the UX/backtesting review

> Status: **accepted 2026-11-18.** The decisions in §7 are folded into
> `docs/BRIEF.md` and `AGENTS.md`; this file remains the implementation map.

## 1. Verdict

- **Adopt:** the single Research Workspace, command-palette market search,
  quick/advanced split, shared indicator instances, signal-vs-fill chart markers,
  run comparison, experiment presets, inline data quality, remembered defaults,
  execution profiles, lifecycle filters, chronological train/test, prediction-
  market metrics, sizing transparency, diagnostics, config separation,
  experiment queue, and caching.
- **Reconcile:** execution uses one user-facing set of five profiles, not a
  tier-plus-preset vocabulary. Sizing labels collapse for a single long binary
  token. Lifecycle phases come from verified ESPN key events, not an assumed
  duration.
- **Defer:** full L2 fill simulation with queue position, walk-forward/CPCV,
  multi-instrument portfolios, and fractional Kelly until a probability source
  exists.
- **Decline:** random cross-validation for time-dependent data, an auto-optimiser
  that promotes a winner, and arbitrary Python strategies.

## 2. The three reconciliations

### 2.1 Execution: one set of five profiles

No tiers in the UI or the docs. Each profile resolves internally to a cost
source (`none`, `fixed`, `dynamic`) that is recorded in the manifest but never
shown as a second vocabulary.

| Profile | Cost source | Fee | Slippage | Minimum absolute | Fallback |
|---|---|---|---|---|---|
| Frictionless | `none` | 0 | 0 | 0 | none |
| Optimistic | `fixed` | venue | 0 | 0 | — |
| Conservative | `fixed` | venue | 10 bps | 0.001 | — |
| Stress | `fixed` | venue | 25 bps | 0.002 | — |
| Book | `dynamic` | venue | book walk | — | Conservative |

Fill price for a token at `c`:

```
slip      = max(c * slippage_bps / 10_000, min_abs_slippage)
buy_fill  = clamp(c + slip, tick, 1 - tick)
sell_fill = clamp(c - slip, tick, 1 - tick)
```

The same formula applies to YES and NO because each token is its own instrument.
The side comes from the open/close intent: closing a long is a sell of the held
token, not a purchase of the complement.

Book falls back to Conservative, never to Frictionless.

### 2.2 Sizing: show the binary arithmetic, drop redundant labels

For a token at `c`, a stake of `S` means:

```
contracts   = floor(S / c)
max_loss    = S + fees
max_payout  = contracts * 1
max_profit  = contracts * (1 - c) - fees
```

For a single long token, "fixed risk" and "% of bankroll" are the same control:
the maximum loss equals the stake. v1 keeps **Fixed stake (£)** and **Risk % of
bankroll**, and shows stake/contracts/max loss/max payout/max profit beside the
input. Do not ship four labels for two behaviours.

Fractional Kelly needs a probability `p`. The rule strategy has a signal, not a
calibrated probability. Using the full-sample win rate is lookahead. v1
therefore omits Kelly; v1.1 may add it with an explicit probability source
(trailing win rate with warm-up, or a calibrated model) and a hard cap.

### 2.3 Lifecycle: verified key events, never a nominal duration

Source of truth: ESPN `keyEvents` entries of type `kickoff` and
`end-regular-time`, with their UTC `wallclock`. The 2026 cache covers 1,006 of
1,007 fixtures with both; pre-2026 is backfilled from the ESPN scoreboard API.
The observed kickoff handles delayed matches.

| Phase | Rule | Source |
|---|---|---|
| `pre_match` | `t < actual_kickoff` | ESPN kickoff wallclock |
| `in_play` | `actual_kickoff <= t < actual_end` | ESPN end-regular-time wallclock |
| `post_match` | `actual_end <= t < settlement` | derived |
| `settled` | `t >= settlement` | market metadata |
| `unknown` | no verified row | no phase filter |

No 120-minute fallback. A fixture without a verified row reports `unknown` and
disables the phase filter; the UI says why.

## 3. Suggestion → implementation map

| # | Decision | Implementation | Phase |
|---|---|---|---|
| 1 | Adopt | One `/` workspace: top market bar, centre chart, right strategy/execution rail, collapsible results drawer; `/data`, `/runs`, `/presets` secondary | P1 |
| 2 | Adopt | Command palette (Ctrl+K) over an instrument index; fuzzy team search; date + coverage badge; YES/NO toggle; remember last | P1 |
| 3 | Adopt | Quick mode = built-in parameter form; advanced = rule tree; same schema and validator | P2 |
| 4 | Adopt | Workspace-level indicator instances; chart and rule builder reference the same instance ids; inline parameter popover | P1 |
| 5 | Adopt | `signals.parquet` + fill markers; distinct shapes for signal/fill/settlement; trade inspector with entry context | P2 |
| 6 | Adopt | Compare 2–3 runs: metric table + normalised equity overlay; `?compare=` URL | P3 |
| 7 | Adopt | Save complete experiment config as a preset; load into workspace; no folders/projects | P3 |
| 8 | Adopt | Precompute coverage/quality per instrument; badge in selector and workspace; preflight warnings | P1/P2 |
| 9 | Adopt | localStorage for last UI state + server presets; URL encodes meaningful state; reset control | P1 |
| 10 | Reconcile | One set of five profiles (§2.1); `min_abs_slippage`; explicit fill timing; sell exits are sells of the held token | P2 |
| 12 | Adopt | Lifecycle operands from `match_lifecycle`: `time_to_kickoff_minutes`, `time_since_kickoff_minutes`, `time_to_settlement_minutes`, `phase` | P3 |
| 13 | Adopt | All/Pre/In-play/Post toggle, kickoff marker, phase metrics table; unknown phase disables the filter | P3 |
| 14 | Adopt | Chronological split + embargo; in/out metrics; holdout-use ledger with warning | P3 |
| 16 | Adopt (bounded) | Experiment grid, max runs/time, heatmap, all runs retained; no optimiser | P4 |
| 17 | Adopt | Core + PM metrics (§4); gross/net separate; method shown in tooltip | P2 |
| 18 | Reconcile | Stake/contracts/max loss panel; two sizing modes; Kelly deferred | P2 |
| 22 | Adopt (small) | Logit, absolute/percentage probability change, rolling probability vol, distance from extremes, rolling volume, trade count, time-to-event | P2/P4 |
| 24 | Adopt | `diagnostics.json` + per-leaf condition truth counts + signal series | P2 |
| 25 | Adopt | Split `strategy` / `execution` / `risk` / `evaluation`; separate strategy hash and run hash | P1 |
| 26 | Adopt | `experiments` parent with child runs; cascade cancel; progress | P4 |
| 27 | Adopt | Content-addressed cache: bars, indicator series, signals, settlement (§5) | P1/P2 |

## 4. Metrics to define explicitly

Core: net P&L, return on initial capital, Sharpe (daily, 365-day annualisation,
rf=0, flat days included), max drawdown, trade count, win rate, average win/loss,
profit factor, expectancy, average holding period, exposure, fees, slippage.

Prediction-market: average entry/exit probability, average probability movement
captured in YES-equivalent points, return per £ capital deployed, return per £
notional traded, average contracts, settlement P&L vs trading P&L, held-to-
settlement count, early exits.

Separate **gross** (before fees/slippage) from **net**. Slippage is embedded in
the fill price; fees are a separate line. Do not subtract either twice.

## 5. Caching keys

| Component | Key |
|---|---|
| Bars | `instrument + bar_type + data_version` |
| Indicator series | `bars_key + spec_hash + indicator_impl_version` |
| Signals | `bars_key + strategy_hash + warmup + indicator_impl_version` |
| Settlement | `instrument + settlement_version` |
| Lifecycle | `match_key + lifecycle_version` |

Signals are independent of execution, sizing and the evaluation date, so a
sweep or a compare reuses them. Compute the full series with a warm-up buffer
and slice; do not recompute per date window. LRU with a size cap and `stats`
endpoint.

## 6. Phases

**P1 — Foundation.** Config split + hashes; instrument index with search and
quality; workspace shell; chart; shared indicator instances; URL state/defaults;
bar and indicator caches.

**P2 — First end-to-end run.** Quick/advanced; five execution profiles + minimum
absolute slippage; sizing transparency; single-run backtest; diagnostics;
signal/fill markers; trade inspector; core metrics; signal cache.

**P3 — Evaluation.** Chronological split + holdout ledger; lifecycle operands,
phase toggle and phase metrics; compare runs; experiment presets.

**P4 — Scale.** Experiment queue; bounded grid/heatmap; PM indicators; settlement
cache; cache stats/LRU.

**P5 — Hardening.** Acceptance suite, Tailscale unit, cancellation/orphan tests,
performance budgets.

Dependency order is deliberate: the split config schema lands before any run
artifact; execution and sizing land before results are trusted; the sweep lands
only after the queue and signal cache exist.

## 7. Decisions

| # | Decision | Outcome |
|---|---|---|
| 1 | Kelly | Deferred to v1.1; no calibrated probability source in v1 |
| 2 | Sizing labels | Two modes: Fixed stake (£) and Risk % of bankroll |
| 3 | Lifecycle source | ESPN `keyEvents` kickoff / end-regular-time wallclock; unknown when absent; no nominal duration |
| 4 | Holdout | Soft warning plus append-only ledger, keyed by strategy hash and split |
| 5 | Sharpe | Daily, 365-day annualisation, flat days included |
| 6 | Sweep limits | Default 50 runs / 15 min; hard caps 200 / 60 min |
| 7 | Book fallback | Book → Conservative, never Frictionless |
| 8 | Compare | 3-run limit, equity normalised to 100 at the common start |

## 8. Deferred or declined

- Full order-book fill simulation with queue position: v2.
- Walk-forward and CPCV: after the single chronological split is proven.
- Multi-instrument portfolios and shared bankroll: v2.
- Random CV: declined; time-dependent data.
- Auto-optimiser/promotion: declined; sweep is a grid, all runs retained.
- Arbitrary Python: declined; declarative rules only.
- Betfair/Kalshi readers: v2, unchanged.
