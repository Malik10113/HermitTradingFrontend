---
title: Import the parallel skill from AlphaCollector
author: Malik10113
date: 2026-11-19
branch: master
id: l-156d9dff
scope: repo
status: active
replaces: l-5fd9ff36
src: agent
touches:
  - ".opencode/skills/parallel/SKILL.md"
  - ".opencode/skills/parallel/references/protocol.md"
  - ".opencode/skills/parallel/references/executor-brief.md"
  - ".opencode/skills/parallel/references/ledger-and-reports.md"
  - ".opencode/commands/parallel.md"
---

## Decided
- The AlphaCollector `/parallel` skill is imported repo-locally at `.opencode/skills/parallel/`
  (OpenCode convention: `SKILL.md` + `references/` + `scripts/`), with the matching slash
  command at `.opencode/commands/parallel.md`, because OpenCode derives the skill id from the
  directory name and loads it on trigger without extra config.
- Tests are scoped per executor: each atom names its domains and runs only
  `scripts/test_domains.sh --domain <domain>` (or one test file). Executors never run `--all`
  or `--changed`, because `--changed` diffs against `HEAD` and selects nothing after the
  executor commits, and `--all` makes every subagent pay for every domain and lets an
  unrelated red test block an atom that cannot fix it. The master runs `--all` before a wave
  merges.
- A `skip` or `nothing to run` line is not a passing check (frontend without `node_modules`,
  e2e without `HTF_E2E`), so the executor must bootstrap the dependency or report `BLOCKED`,
  otherwise a green report can hide zero executed tests.
- AlphaCollector-specific assumptions were remapped: the gate is `scripts/test_domains.sh`
  instead of `make check`, ledger examples cite `backend/`, `frontend/` and `tests/`, and
  policy/snapshot forbiddens became `var/`, `.trail/`, `.venv/` and `node_modules/`.
- `/root/FundedBetBacktesting/` is an explicit forbidden write set in `executor-brief.md`
  because AGENTS.md §2 makes it read-only, and without it an executor could write upstream.

## Watch out
- The skill's `plan_check.mjs` and its 22 node tests are vendored; run
  `node scripts/test_plan_check.mjs` from the skill directory after editing the checker.
- Executors never push; the master pushes per AGENTS.md §11 after the gate and `trail check`.
