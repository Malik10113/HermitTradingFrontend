# Executor brief template

One file per atomic task, written by the master before dispatch, stored at
`/tmp/opencode/parallel/<run>/briefs/<task-id>.md`. The dispatch prompt is only a pointer
to this file. Fill every field; an empty field means the task is not atomic yet.

```markdown
# Task <F1-E2> — <one-sentence intent>

You are an executor for feature <F1 "intent">. Work only inside the boundary below.

## Boundary
- Worktree: <absolute path>            (created by master, detached HEAD at <SKELETON sha>)
- Branch: pll/<run>/<F1-E2>            — your first command: `git switch -c pll/<run>/<F1-E2>`
- Bootstrap: fixtures <paths already copied read-only>; env <file>; PORT <n>; DB <path>
- Write set (allowed, exclusive):
  - <path>#<symbol or line range>
  - <path2>
- Read-only (do not modify): <paths>
- Forbidden: any path outside the worktree; `var/`; `.trail/`; `.venv/`; `node_modules/`;
  `/root/FundedBetBacktesting/`; other worktrees; `git push`, `git tag`, `git rebase`,
  `git reset --hard` outside your own branch.

## Context you need (self-contained)
- Base commit: <BASE>
- Skeleton: merged at <SKELETON sha>; signatures are frozen. If your work needs a different
  signature, report `BLOCKER kind=missing-contract` and stop — do not edit the signature.
- Relevant existing code: <path:lines, 1-3 bullets on how it works today>
- Contract you must honor: <signature / type / schema / CLI shape, verbatim>
- Predecessor: <task id and commit already merged, or "none">

## Work
0. Confirm the skeleton imports and your write set is the only thing you change.
1. Read the files in the write set.
2. <Specific change, in 1–4 steps. No design decisions left open.>
3. Run: <exact acceptance command>
   Expected: <exact expected output or assertion, e.g. "OK" with N tests, or specific
   new test name passing>
4. Stage exactly the write set by path (`git add <path> …`), never `git add -A`;
   `git status --porcelain` must show only write-set paths before you commit.
5. Commit once, message: `<type>(<scope>): <summary>`

## Do not
- Do not fix unrelated failures, refactor beyond the write set, reformat untouched code,
  add dependencies, or change public signatures not listed in the contract.
- Do not amend, squash into other commits, or create more than one commit.
- Do not push. Do not start another subagent (the tool does not exist for you).

## REPORT
End your final message with exactly this block:

REPORT <F1-E2> <DONE|BLOCKED|PARTIAL> <commit-sha|-> 
files: <comma-separated paths, with line ranges>
check: <command> -> <pass/fail + key line of output>
deviations: <none | what you changed vs the brief and why>
blocker: <none | kind: brief-defect|missing-contract|collision|external|test-failure;
          what is missing, from whom, and what you would need to finish>
risk: <none | the one thing most likely to break later>

Keep it under 200 words after the header line. Never report DONE without a commit and a
passing check.
```

## Rules for the brief author

- The write set is contractual. Two in-flight briefs may not share a path; same-file atoms
  must name disjoint symbols and a fixed hunk order.
- Cite the skeleton commit in every brief and treat signatures as frozen. A needed signature
  change is a `missing-contract` blocker, not an edit by the executor.
- Declare the bootstrap (fixtures, env, port, database) explicitly. Two executors must never
  share a writable database or a port; a worktree without its fixtures produces failures
  that look like bad atoms.
- Every acceptance check must be runnable in the worktree as written, with absolute or
  repo-relative paths that exist after `git switch -c` and the bootstrap.
- If the task needs data under `var/`, say how to materialize it read-only; do not let the
  executor write to the real store.
- If you cannot state the contract verbatim, the task is not atomic: land the skeleton or a
  spine change first.
- One commit per brief. If the change needs two commits, it is two briefs or a sequence.
- Never put README or docs in a facade atom's write set. An atom that imports three or more
  other atoms is a wave-2 facade; docs are an import-free wave-1 atom of their own.
- Say "stage by path, never `git add -A`" in every brief. The usual contamination is
  `__pycache__/`, build output, or an editor swap file: a real executor committed `.pyc`
  files on the first try and had to rebuild the commit.
