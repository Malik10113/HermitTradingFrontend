---
name: parallel
description: Use when the user runs /parallel or asks for a massively parallel multi-worktree implementation run. Decomposes one high-level change into atomic, non-overlapping tasks, lands a frozen skeleton, dispatches one thinly scoped subagent per task in its own bootstrapped worktree, integrates results as they land, runs a clean-context reviewer, routes blockers to the owning feature, and reports progress per feature.
compatibility: opencode
metadata:
  workflow: parallel-delivery
  audience: operators
  source: AlphaCollector .opencode/skills/parallel (imported 2026-11-19)
---

# Parallel execution mode

One high-level request becomes features, each feature becomes atomic tasks, each atomic task
becomes one executor subagent in its own worktree, and every result is integrated, verified,
reviewed, and reported. Correctness comes from task boundaries, not review thoroughness: if two
executors can touch the same line, the split is wrong.

Read `references/protocol.md` before the first dispatch. It carries the phase detail (preflight,
decompose, skeleton, dispatch math, blocker routing, integration, review, reports, cleanup).
This file is the operating contract.

## Depth ceiling (hard constraint)

`experimental.subagent_depth` is `1`: only the master can call `subagent`. An executor that
tries to launch another has no such tool and the run stalls.

| Level | Reality | Identity |
| --- | --- | --- |
| Master | This session (primary agent) | `M` |
| Feature agent | **Persona** held by the master: a brief, a ledger slice, an owner | `F1`, `F2`, … |
| Executor | Real child session, agent `implementer`, depth 1 | `F1-E1`, `F1-E2`, … |
| Reviewer | Real child session, agent `reviewer`, depth 1, clean context | `R1`, `R2`, … |

A feature agent is a hat the master wears, not a session. Owning a change, atomizing it,
accepting reports, and deciding blocker routing are performed by the master *as* that feature.
If `subagent_depth` ever becomes `2`, this protocol maps 1:1 onto real feature sessions.

Use `implementer` for executors, `reviewer` for reviewers and `recon` for read-only recon. All
three are pinned to the same cheaper model for this skill only, so a parallel run never spends
the master's model on a child. `explore` and `general` are not used here; every subagent call
made outside this skill keeps the master's model.

## Ownership and ledger

The ledger is the source of truth: `/tmp/opencode/parallel/<run-id>/ledger.json`. Never put it
in the repo. After compaction, re-read it; do not reconstruct state from the transcript.

```
M
├── F1  "cache invalidation"           intent, acceptance, tasks[], status
│   ├── F1-S   skeleton (signatures, fixtures)  merged first, frozen
│   ├── F1-E1  atom 1  worktree <wt>/f1-e1  branch pll/<run>/f1-e1
│   └── F1-E2  atom 2  ...             hedge: F1-E2b when predicted tail
└── F2  "export format"                blocked-by: F1-E1
```

Schemas and report formats: `references/ledger-and-reports.md`. Briefs:
`references/executor-brief.md` and `references/reviewer-brief.md`.

## Gate — run this before anything else

Parallelize only if **all four** hold:

1. At least two atoms are independently mergeable, each with its own acceptance check.
2. The serial spine — skeleton, contracts, acceptance harness — is under ~40% of the work.
3. At least two atoms can start in the same wave, from the same merged base.
4. The acceptance harness and each atom's domain check exist and are frozen; neither executors
   nor the reviewer may edit them.

If any fails: name the failing condition in one line, implement in this session, stop. Measured
basis: five executors took 731 s against 245 s single-agent on a 1.5k-LOC project, both green.
Small or tightly coupled work must not be parallelized.

## The run at a glance

1. **Preflight** — clean tree, pin `BASE`, baseline gate, run id, resource plan.
2. **Decompose** — atomicity test, split ladder, facade rule, `plan.tsv`.
3. **Skeleton** — one ≤80-line commit of signatures and fixtures, merged and frozen.
4. **Dispatch** — plan check, longest first, one brief per atom, one worktree per writer.
5. **Collect** — merge each `DONE` immediately, one pick and one gate at a time.
6. **Review** — one clean-context reviewer per wave on the diff plus acceptance script.
7. **Report and clean up** — per feature on DONE, major chunk, or stall; then remove worktrees.

## Worktrees

One writer per worktree, one worktree per concurrent writer.

| Tier | When | Mechanism |
| --- | --- | --- |
| **A** (default) | Any task that writes code | Own bootstrapped worktree |
| **B** | Read-only, or writes file-disjoint from every in-flight task | Main checkout, ledger-noted |
| **C** (forbidden) | — | Shared write set or worktree; writes outside the worktree; shared writable database or port |

Three facts that cause most failures: worktrees are **detached HEAD**, so an executor's first
command is `git switch -c pll/<run>/<task>`; a worktree has no ignored fixtures, so bootstrap
them read-only; and paths in briefs must be absolute. Detail in `references/protocol.md`.

## Non-negotiables

- Do not write feature code while two or more executors are in flight. Exceptions: merge
  conflicts, skeleton changes, one-line verification fixes.
- Do not dispatch without a brief on disk, or accept a report lacking a commit, a check, and a
  file list.
- Do not let the reviewer edit, or an executor review its own diff.
- Do not invent atoms to fill slots. Idle slots are cheaper than collisions.
- Scope tests per executor: only the domains its write set touches
  (`scripts/test_domains.sh --domain <domain>`, `--fast` when the brief says so). Never `--all`
  from a subagent, and never `--changed` in a worktree: it diffs against `HEAD`, so it selects
  nothing once the executor commits. The master runs `--all` before a wave merges. A `skip` or
  `nothing to run` line from the runner is not a pass: fix the bootstrap or report `BLOCKED`.
- Do not push, tag, or touch `var/` or `.trail/` unless the feature is about them. Executors
  never push; only the master pushes, and only after the gate and `trail check` are clean.
- `/root/FundedBetBacktesting/` is read-only upstream data: never a write set, never a
  bootstrap copy target. Generated state lives under `var/` only.

## Plan check

Write `plan.tsv` beside the ledger (id, wave, kind, imports, write_set — tab-separated) and run,
from this skill's base directory:

```sh
node scripts/plan_check.mjs /tmp/opencode/parallel/<run>/plan.tsv
```

R1: a wave-1 atom imports nothing another atom owns. R2: a facade (3+ imports) is wave 2 and
docs-free. R3: write sets disjoint. R4: docs ship in wave 1. Exit 2 = malformed plan, exit 1 =
rule violation. Fix the plan, not the linter.

## Quick reference

```text
gate first: ≥2 mergeable atoms, spine < 40%, two atoms start together, harness frozen
M → F persona → implementer subagent (depth 1) — never deeper
skeleton F1-S: signatures + fixtures, ≤80 lines, merged before wave 1, then frozen
atomic = 1 intent + exclusive write set + 1 check + clean cherry-pick + 1 revert
worktree per writer; bootstrap fixtures/ports/DBs; detached ⇒ git switch -c
executor tests: --domain <d> only, never --all or --changed; master runs --all before merge
wave 1 = no cross-atom imports;  facade (≥3 imports) is wave 2 and docs-free;  docs ride wave 1
dispatch longest first;  launch while d ≥ 2c, backlog < 3, n < N_cap, width still pays
integrate as it lands: one pick, gate it, green advances, red resets and routes
one clean-context reviewer per wave: diff + acceptance only, verdict, no edits
report per feature on DONE, major chunk, or stall; name the delay cause
```

## Files

| Path | Purpose |
| --- | --- |
| `SKILL.md` | This contract: gate, roles, worktree rules, non-negotiables |
| `references/protocol.md` | Phase-by-phase procedure, dispatch math, blocker table |
| `references/executor-brief.md` | Fill-in brief for one atomic task |
| `references/reviewer-brief.md` | Fill-in brief for the clean-context reviewer |
| `references/ledger-and-reports.md` | Ledger schema, `plan.tsv`, report templates |
| `scripts/plan_check.mjs` | Plan validator (Node, no dependencies) |
| `scripts/test_plan_check.mjs` | Tests for the validator |
