# Review plan — response to the UX/backtesting review

> Status: **proposal, not yet binding.** This maps each review suggestion to a
> decision, an implementation shape and a phase. Once the open decisions in §7
> are answered, the accepted parts move into `docs/BRIEF.md` and `AGENTS.md`.

## 1. Verdict

- **Adopt:** the single Research Workspace, command-palette market search,
  quick/advanced split, shared indicator instances, signal-vs-fill chart markers,
  run comparison, experiment presets, inline data quality, remembered defaults,
  execution presets, lifecycle filters, chronological train/test, prediction-
  market metrics, sizing transparency, diagnostics, config separation,
  experiment queue, and caching.
- **Reconcile:** execution profiles (review §10) are presets over the existing
  None/Fixed/Dynamic tiers, not a second execution system. Sizing labels (review
  §18) collapse for a single long binary token. Lifecycle phases (review §12–13)
  are exact only where ESPN events exist (2026); pre-2026 ends are nominal.
- **Defer:** full L2 fill simulation with queue position, walk-forward/CPCV,
  multi-instrument portfolios, and fractional Kelly until a probability source
  exists.
- **Decline:** random cross-validation for time-dependent data, an auto-optimiser
  that promotes a winner, and arbitrary Python strategies.

## 2. The three reconciliations

### 2.1 Execution: tiers stay, profiles become presets

The tier is *where the cost comes from*; the preset is *a named bundle of
parameters*. This avoids a 3×3 matrix of engines.

| Preset | Tier | Fee | Slippage | Minimum absolute | Use |
|---|---|---|---|---|---|
| `frictionless` | `none` | 0 | 0 | 0 | null baseline |
| `optimistic` | `fixed` | venue | 0 | 0 | best case |
| `conservative` | `fixed` | venue | 10 bps | 0.001 | default |
| `stress` | `fixed` | venue | 25 bps | 0.002 | robustness |
| `book` | `dynamic` | venue | book walk | — | 2026 only |

Fill price for a buy on token price `c`:

```
slip = max(c * slippage_bps / 10_000, min_abs_slippage)
fill = clamp(c + slip, tick, 1 - tick)      # sell: c - slip
```

The same formula applies to YES and NO because each token is its own instrument;
the economic direction is carried by the position side, not by a short flag.

`dynamic` falls back to `conservative`, not to a bare fixed config, and never to
`frictionless`. This makes the fallback explicit and comparable.

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

### 2.3 Lifecycle: exact in 2026, nominal before it

Kickoff is available for every match in `big4_matches.parquet`. Event end is
exact only from the 2026 ESPN cache. Define:

| Phase | Rule | Source |
|---|---|---|
| `pre_match` | `t < kickoff` | exact |
| `in_play` | `kickoff <= t < kickoff + duration` | ESPN end in 2026; nominal 120 min before |
| `post_match` | `end <= t < settlement` | derived |
| `settled` | `t >= settlement` | exact from market metadata |

Every bar carries `phase` and `phase_source` (`espn` | `nominal`). A pre-2026
in-play result is labelled approximate; the UI says so. Do not silently present
a nominal phase as measured.

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
| 10 | Reconcile | Presets over tiers (§2.1); `min_abs_slippage`; fill timing explicit | P2 |
| 12 | Adopt | Lifecycle operands in rules: `time_to_kickoff_minutes`, `time_since_kickoff_minutes`, `time_to_settlement_minutes`, `phase` | P3 |
| 13 | Adopt | All/Pre/In-play/Post toggle, kickoff marker, phase metrics table | P3 |
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

Signals are independent of execution, sizing and the evaluation date, so a
sweep or a compare reuses them. Compute the full series with a warm-up buffer
and slice; do not recompute per date window. LRU with a size cap and `stats`
endpoint.

## 6. Phases

**P1 — Foundation.** Config split + hashes; instrument index with search and
quality; workspace shell; chart; shared indicator instances; URL state/defaults;
bar and indicator caches.

**P2 — First end-to-end run.** Quick/advanced; execution presets + min absolute
slippage; sizing transparency; single-run backtest; diagnostics; signal/fill
markers; trade inspector; core metrics; signal cache.

**P3 — Evaluation.** Chronological split + holdout ledger; lifecycle operands,
phase toggle and phase metrics; compare runs; experiment presets.

**P4 — Scale.** Experiment queue; bounded grid/heatmap; PM indicators; settlement
cache; cache stats/LRU.

**P5 — Hardening.** Acceptance suite, Tailscale unit, cancellation/orphan tests,
performance budgets.

Dependency order is deliberate: the split config schema lands before any run
artifact; execution and sizing land before results are trusted; the sweep lands
only after the queue and signal cache exist.

## 7. Decisions required

1. **Kelly:** confirm deferral to v1.1, or name the probability source now.
2. **Sizing labels:** confirm two modes (fixed £ stake, risk % of bankroll) and
   drop "fixed risk" as a separate label.
3. **Pre-2026 phases:** confirm nominal 120-minute in-play end with a visible
   `phase_source: nominal` flag.
4. **Holdout:** confirm a one-shot ledger per `(universe, strategy_hash, split)`
   that warns on the second reveal.
5. **Sharpe:** confirm daily, 365-day annualisation, flat days included.
6. **Sweep limits:** confirm default 50 runs / 15 min, hard caps 200 / 60 min.
7. **Dynamic fallback:** confirm `book → conservative` (fixed) rather than a bare
   fixed config.
8. **Compare:** confirm 3-run limit and equity normalised to 100 at the common
   start.

## 8. Deferred or declined

- Full order-book fill simulation with queue position: v2.
- Walk-forward and CPCV: after the single chronological split is proven.
- Multi-instrument portfolios and shared bankroll: v2.
- Random CV: declined; time-dependent data.
- Auto-optimiser/promotion: declined; sweep is a grid, all runs retained.
- Arbitrary Python: declined; declarative rules only.
- Betfair/Kalshi readers: v2, unchanged.
