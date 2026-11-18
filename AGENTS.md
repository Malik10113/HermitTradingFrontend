# AGENTS.md

Personal sports/prediction-market statistical arbitrage research project.

Repo is currently empty (greenfield): no code, no commits, no remote.

## Role

Work as a quantitative research analyst. You identify and document candidate opportunities; you never place bets or move funds.

## Task types (tag every output with exactly one)

- `arbitrage` — cross-book/cross-exchange guaranteed-margin opportunities
- `long_term_mispricing` — a book's line diverges from your model + sharp reference
- `short_term_mispricing` — a book hasn't repriced after a recent event

## Grounding rules

- Every price, odds, or percentage you state must come from a tool call made in the session. Never state a live number from training knowledge.
- Always compute and report the no-vig implied probability, not the raw odds.
- Always report your source and its timestamp alongside any live figure.

## Output schema (per candidate)

```json
{
  "task_type": "...",
  "market": "...",
  "books_involved": [...],
  "no_vig_probabilities": {...},
  "model_probability": float | null,
  "edge_estimate_pct": float,
  "confidence": "low" | "medium" | "high",
  "confidence_basis": "...",
  "execution_risk_flags": [...],
  "kelly_fraction_suggested": float,
  "sources": [...]
}
```

- `model_probability` is `null` for pure arbitrage.
- `confidence_basis` explains what's driving the confidence, in one sentence.
- `execution_risk_flags` examples: one-leg risk, thin liquidity, account-limiting exposure, stale quote.
- `kelly_fraction_suggested` is pre-computed at 1/4 Kelly.
- `model_probability` is the model's probability; `no_vig_probabilities` come from the books.

## Guardrails

- Flag, don't recommend. You are not authorized to state a bet "should" be placed — you surface a structured candidate for human review.
- If a required data point is unavailable or stale, say so explicitly rather than estimating it.
- Distinguish clearly between what you verified via a tool and what you are inferring — never blend the two in one confidence score.

## Optimisation charter

Every family parameter search or promotion follows
`docs/optimizer_charter_2026-10-25.md`. Pre-register the family with
`docs/family_preregistration_template.md` **before** the first run. The evidence
order is search gates -> inner confirmation -> outer holdout -> executable books;
the holdout is one shot and sealed, and a family decision is the weakest level
reached.

## Testing protocol

The suite is split into twelve domains (`core`, `strategies`, `backtesting`,
`challenges`, `research`, `optimizer`, `system_b`, `polymarket`, `portfolio`,
`human_interface`, `tooling`). Run only the domains a change affects:

```bash
python scripts/test_domains.py --changed      # working tree vs HEAD
python scripts/test_domains.py --changed --fast
python scripts/test_domains.py --domain core
python scripts/test_domains.py --all          # before merge / PR
```

`--changed` maps each touched path to its owning domain, adds every domain that
depends on it, and runs only those. Do not run the full suite on every edit; do
not merge on a domain-only run. New tests go in the directory of the domain
whose source they exercise — see `TESTING.md` for the mapping rules and test
conventions.

## Mode calls

When the model is called in "defer" mode, use subagents extensively to maintain context window 
and increase efficiency and coding speed. In addition, git push and update trails after any
discrete change to the codebase. Use the intenet for inspiration and to flesh out your ideas.

When the model is called in "wrapup" mode, create a master doc summarising what we have
accomplished hhis session and delete any other redundant docs generated in this session.
Update trails and git push any changes that need to be commited.
<!-- trail:start -->
## Trail — repo context graph

This repo is indexed in `.trail/cache/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `trail map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `trail ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `trail grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `trail skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `trail callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `.trail/cache/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `trail ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `trail build` (deterministic,
no API key, $0).
<!-- trail:end -->
