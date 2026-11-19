---
title: Import the parallel skill from AlphaCollector
author: Malik10113
date: 2026-11-19
branch: master
id: l-5fd9ff36
scope: repo
status: active
src: agent
---

## Decided
- The AlphaCollector `/parallel` skill is imported repo-locally at `.opencode/skills/parallel/`
  (OpenCode convention: `SKILL.md` + `references/` + `scripts/`), with the matching slash
  command at `.opencode/commands/parallel.md`, because OpenCode derives the skill id from the
  directory name and loads it on trigger without extra config.
- AlphaCollector-specific assumptions were remapped to this repo: the wave gate is
  `scripts/test_domains.sh` instead of `make check`, ledger examples cite `backend/`,
  `frontend/` and `tests/` paths, and policy/snapshot forbiddens became `var/`, `.trail/`,
  `.venv/` and `node_modules/`, otherwise the first baseline gate fails with a missing
  `make` target.
- `/root/FundedBetBacktesting/` is an explicit forbidden write set in `executor-brief.md`
  because AGENTS.md §2 makes it read-only, and without the line an executor could write into
  the upstream data repo.

## Watch out
- The skill's own `plan_check.mjs` and its 22 node tests are vendored; run
  `node scripts/test_plan_check.mjs` from the skill directory after editing the checker.
- Executors must never push; the master pushes per AGENTS.md §11 after the gate and
  `trail check`. The import rewrote the original blanket "do not push" to say that.
