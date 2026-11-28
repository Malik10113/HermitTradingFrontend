# Protocol — /parallel in detail

Load this file after the skill triggers and before the first dispatch. It carries the phase
detail; `SKILL.md` carries the operating rules and the gate. Paths below are relative to this
skill's directory.

## Phase 0 — Preflight

### 0.1 Facts

1. `git status --porcelain` in the canonical checkout. Dirty tree: record the paths, and
   exclude them from every write set; never stash or revert operator work.
2. Pin `BASE=$(git rev-parse --short HEAD)` and the branch. All worktrees start there.
3. Baseline verification (`scripts/test_domains.sh --all`, or the narrowest domain set the run
   needs). A red baseline means the run starts blocked; report it and stop dispatching.
4. `RUN=$(date +%m%d-%H%M)`; create
   `/tmp/opencode/parallel/$RUN/{ledger.json,plan.tsv,briefs,reports,diffs}`.
5. Record the repo's verification commands from `AGENTS.md` and map each atom's write set to
   its domains. Executors verify with `scripts/test_domains.sh --domain <domain>` (add
   `--fast` only when the brief says so) or a single test file. Never `--all`, and never
   `--changed`: it diffs against `HEAD`, so inside an executor worktree it selects nothing
   after the executor commits. `--all` belongs to the master's pre-merge gate.
6. Record per-worktree resources: fixture paths, env files, port range, database paths, and
   test dependencies (`frontend/node_modules`, the Python env).

## Phase 1 — Decompose (master, as each feature agent)

For each feature, produce an `F` brief: intent in one sentence, observable acceptance
criterion with the domain-scoped command that proves it, subsystems touched, dependency edges,
and the list of candidate atoms. Then turn each candidate into an atomic task.

### Atomicity test

A task is atomic only if **all five** hold:

1. **One intent** — one sentence, one commit, one owner.
2. **Exclusive write set** — no other in-flight task writes any path in it.
3. **Independently verifiable** — one command with an expected result, no dependency on
   another unmerged branch.
4. **Clean on the base** — cherry-picking it onto `BASE` (or onto the merged predecessors)
   produces no conflict; for same-file atoms, the hunks are non-adjacent (different symbols,
   ≥ 10 lines apart).
5. **One-revert reversible** — reverting that single commit restores a green tree.

If any fails, split. Split ladder, in order:

1. By file.
2. By symbol: function, class, adapter, command.
3. By direction: land the **contract** (signature, type, schema, protocol constant) first as
   the skeleton, then parallelize the bodies against it.
4. By call site, or by artifact type: implementation / tests / docs.
5. By layer: data → domain → interface.
6. Last resort: sequence them under one owner as two commits.

Never split mid-function. Never create a "clean up whatever is broken" task; that is a
blocker, and blockers get routed, not delegated blind.

Docs are an artifact type, not part of an implementation atom. In the measured run a README
sat on the slowest executor's clock and cost 33 s of tail for no coupling reason.

### Facade rule (wave assignment)

An atom that imports modules owned by three or more other atoms is a **facade**: it cannot
start until those modules merge, because its worktree contains only the skeleton. Facades are
wave-2 atoms, and their write set never includes documentation. A brief that says "and write
the README" on a facade is a scheduling defect, not a convenience.

Docs are import-free, so they go in their own wave-1 atom beside the code atoms. If they
describe APIs that do not exist yet, write them against the frozen spec and let the facade atom
correct them; a docs atom never waits on a merge.

Measured: in the pipeline-lab run the CLI atom imported four atoms and carried the README. It
cost 219 s, of which 78 s was reading everyone else's modules and 28 s was the docs. Splitting
the docs out removes ~55 s from the critical path at zero coupling cost.

### Plan check before dispatch

Write the plan beside the ledger as tab-separated lines:

```text
# id	wave	kind	imports	write_set
F1-E1	1	atom	-	backend/data/sources.py tests/data/test_sources.py
F1-DOC	1	docs	-	docs/DATA_TRACE.md
F1-E5	2	facade	F1-E1,F1-E2,F1-E3	backend/api/routes.py tests/api/test_routes.py
```

Then, from this skill's base directory:

```sh
node scripts/plan_check.mjs /tmp/opencode/parallel/<run>/plan.tsv
```

It enforces R1 (a wave-1 atom imports nothing another atom owns), R2 (a facade is wave 2 and
docs-free), R3 (write sets disjoint), R4 (docs ship in wave 1). Fix the plan, not the linter.
Exit 2 means the plan file is malformed; exit 1 means a rule is violated.

### Feature-level parallelism

Features are worth separating when their write sets are disjoint by subsystem *and* they have
no acceptance dependency. A cross-feature contract makes one of them a predecessor: encode it
as an edge, not as hope.

## Phase 2 — Skeleton (walking skeleton, one serial atom)

Before the first wave, land `F1-S`: the smallest commit that makes every atom independent.

- Package entry point, exported names, exception classes, dataclasses, and every public
  signature raising `NotImplementedError`.
- Shared test fixtures and the frozen acceptance harness path.
- No behavior, no tests beyond an import smoke check. Budget ≤ ~80 lines and one commit.
- Merge it, run the gate, and record the commit as `SKELETON` in the ledger.
- Lazy-export leaf modules from the package root so the skeleton imports before they exist.
  A skeleton that fails `import` blocks the whole run; that happened once and cost a rebuild.

Every later brief cites the skeleton commit and states that signatures are frozen. An executor
that needs a different signature reports `BLOCKER kind=missing-contract` and stops; the master
lands the signature change as a spine task and re-dispatches. This is the one case where the
master writes code.

Why this phase exists: a five-atom run spent 254 s on its first atom because tests cannot
import what does not exist, so nothing else could start. A skeleton moves that cost to ~90 s
and lets the widest atom start first.

## Write sets and worktrees

Every task carries an explicit write set: paths plus symbol names. Overlap is decided on that
set before dispatch, never discovered at merge time.

| Tier | When | Mechanism |
| --- | --- | --- |
| **A** (default) | Any task that writes code | Own bootstrapped worktree |
| **B** | Read-only recon; or writes file-disjoint from every other in-flight task and the verification path has no tree-wide autoformatter | Main checkout, ledger-noted |
| **C** (forbidden) | — | Two executors sharing a write set in one checkout; two executors in one worktree; an executor writing outside its worktree; two executors sharing a writable database or port |

Tier A mechanics:

```text
worktree_create(name="<run>-<task>")      → absolute directory, detached HEAD at BASE
bootstrap: fixtures, env, PORT, DB        → see below
executor: git switch -c pll/<run>/<task>  (must be its first command)
executor: work, verify, exactly one commit
master:   cherry-pick onto the trunk and gate it (Phase 5)
master:   worktree_remove(directory) after merge, then git branch -D
```

`git branch -d` refuses after a cherry-pick: the new commit is a copy, so git does not see the
branch as merged. Verify the cherry-pick is in `git log` and the gate is green, then delete
with `-D` and record the commit in the ledger.

### Bootstrap (environment parity)

A worktree is a clean checkout, not a working environment. Before dispatch, give every worktree
what the repo's tests actually need, and declare it in the brief:

| Resource | Rule |
| --- | --- |
| Fixtures ignored by git (`var/`, `data/`) | `cp -a` read-only into the worktree; never symlink a writable store |
| Env files (`.env`, `.env.test`) | Copy the test variant; never the secret-bearing one |
| Ports | Allocate `PORT_BASE + index` per atom; two live servers must never share a port |
| Databases | Path inside the worktree; a shared writable DB is a Tier C violation |
| Test dependencies | `frontend/node_modules` installed per worktree (or copied read-only); Python via `uv run` in the worktree; a skipped domain is not a pass |
| Generated caches | Leave absent; the executor's first run creates them |

Record the allocation in the ledger and repeat it in the brief. In the measured run the
worktrees lacked `var/` (harmless there) and had no port policy (harmless only because the
tests bound port 0). In a real repo either omission produces a failure that looks like a bad
atom.

Other notes that matter:

- OpenCode worktrees are **detached HEAD, no branch**. An executor that commits without
  `git switch -c` leaves an unreachable commit; the brief must make the branch its step 1.
- Same file, different regions ⇒ two worktrees, hunks pinned by symbol and line range in both
  briefs, and a fixed merge order decided up front (lowest hunk first).
- Executor paths must be absolute: children resolve relative paths against their own session
  directory.
- Every brief stages by path (`git add <paths>`), never `git add -A`. Untracked build and cache
  artifacts are the most common way a clean atom becomes a dirty commit. Observed live: an
  executor committed `.pyc` files first and had to rebuild the commit.

## Phase 3 — Dispatch

1. Run the plan check (`node scripts/plan_check.mjs <plan.tsv>`). A plan that fails R1–R4 is
   not dispatched.
2. Launch executors in parallel in one assistant turn. Each launch is one `subagent` call:
   `agent: implementer`, `description: "<task id> <3 words>"`, `prompt` = pointer to the brief.

Write the brief to `/tmp/opencode/parallel/<run>/briefs/<task>.md` first, then send:

```text
Read /tmp/opencode/parallel/<run>/briefs/<task>.md and execute it exactly.
Report using the REPORT block in that file. Do not edit outside your worktree.
```

### Order: longest first

Sort ready atoms by estimated duration, descending, and dispatch in that order. Estimate from
write-set size plus artifact types; docs and test files add time, and a file the executor must
write from scratch costs more than an edit.

Reason: wall clock is set by the slowest member. In the measured run one atom (884 lines across
code, tests, and docs) ran 309 s and set the wave's duration while shorter atoms finished
early. Starting it first overlaps it with everything else.

### Marginal dispatch test

Let `c` be the fixed cost of one executor round trip (write brief + ingest report + review diff
+ integrate + verify), and `d` the estimated duration of the candidate task, both measured with
`date +%s` around real work; seed `c ≈ 10 min` of agent time.

Dispatch the next executor only while **all** hold:

1. `d ≥ 2c` — the task pays for its own overhead.
2. `|ready| ≥ n+1` — there is real independent work, not invented splits.
3. `unmerged backlog < 3` — integration is keeping up; otherwise integrate, do not dispatch.
4. `n < N_cap` for the run's shape:

| Shape | `N_cap` |
| --- | --- |
| One feature, atoms inside one file | 3 |
| One feature, disjoint files | 6 |
| 2–4 features, disjoint subsystems | 8 (≤ 3 per feature) |
| Read-only recon or docs | 12 |

5. The previous width increment actually shortened the wave. Track `T(wave)` in the ledger. If
   widening from `n` to `n+1` left `T` unchanged across two waves, the run is saturated: hold
   width and fix the serial spine (skeleton, integration, or verification).

`Δgain = C(n) - C(n+1)`; when that hits zero the `n+1`th executor costs more than it returns.
Amdahl applies — if the serial share of the work is `s`, useful width tops out near `(1-s)/s`.

### Straggler hedge (at most one per run)

When one atom is predicted to exceed the run's p75 duration, dispatch it twice: same brief, two
branches, two worktrees, both from the same base. Keep the better result — gate result first,
then the smaller diff, then the reviewer verdict — and discard the other branch unmerged. Never
merge both, never hedge more than one atom per run, and never hedge an atom whose output
another atom is waiting on if a second copy would delay that dependent.

Hedging is the only duplication the run permits. It buys a quality choice on the one atom most
likely to be the tail, for one extra executor.

## Phase 4 — Collect and route blockers

On each executor report: update the ledger, then act by status.

`DONE` → integration queue, ordered by dependency then by same-file hunk order.
`PARTIAL` → treat as `BLOCKED` with the missing piece named.
`BLOCKED` → route by kind; do not let the executor widen its own scope.

| Blocker kind | Owner | Action |
| --- | --- | --- |
| Ambiguous or under-specified brief | Same `F` | Rewrite narrower; dispatch a **new** executor (fresh context) with a tighter write set and acceptance check. Retry the same brief at most once. |
| Missing contract / interface / schema | Seam owner — the skeleton atom's feature | Signature change as a spine task; dependents parked; re-dispatch them after it merges. Master writes this one. |
| Same-file collision found late | `F` owning the contested region | Serialize: one writer proceeds, the other parks; or re-split by symbol. Never "merge and hope". |
| Cross-feature dependency | `F` owning the depended-on code (new `F` if unowned) | Record the edge; park dependents; dispatch the blocker task under the **owner**, not the blocked feature. |
| External: network, missing data, credentials | Master | One retry maximum, then park the feature and report the delay with its cause. |
| Verification failure after merge | `F` of the reverted commit | Revert the pick, re-dispatch narrower with the failing check as the acceptance criterion. |

A blocker is resolved by assigning a new executor to the appropriate feature agent: same feature
when the fix lives inside the blocked task's own write set, the owning feature when it lives
across a boundary. Route to ownership, not to proximity.

## Phase 5 — Integrate as it lands

Never wait for a wave to finish before merging finished atoms. Integration is a queue drained on
every report, not a phase barrier.

```sh
git cherry-pick -x pll/<run>/<task>      # one pick at a time, never two
<the atom's domain check>                # always: scripts/test_domains.sh --domain <d>
scripts/test_domains.sh --all            # before the wave merges or a push, never per executor
```

Rules:

- One pick in flight. Gate it, then take the next. Two picks before a gate hide which one broke
  the trunk.
- Green: record the merge commit in the ledger, drain the next.
- Red: `git reset --hard HEAD~1` if it is the tip, else `git revert --no-edit <sha>`, log the
  failure, route it as a blocker. Never leave the canonical checkout red between picks.
- A `BLOCKED` atom does not hold the queue. Park it, integrate the rest, re-dispatch later.
- Keep an integration log line per pick (`task, branch, merge sha, gate, seconds`) so the report
  can cite it and so a bisect has a starting point.

This is where the measured run lost its tail: four atoms finished inside 310 s, but nothing
merged until the slowest report arrived, then ~95 s of serial integration ran at the end. Note
that a harness which returns all subagent results in one batch degenerates this step back into
a batch: the queue only helps when reports stream in.

## Phase 6 — Review a wave, clean context only

After a wave's integrations are green, dispatch exactly one reviewer: a `subagent` call with
`agent: reviewer`, `description: "<wave> review"`, and a `prompt` naming the base commit.

- Input: `git diff <BASE>..HEAD`, the frozen acceptance script, and the feature intent in one
  line. **Not** the briefs, **not** the executor reports, **not** the ledger.
- Output: findings with `path:line`, severity (`blocker` / `major` / `minor` / `nit`), and a
  verdict. No edits, no merges, no new files.
- `blocker` or `major` → route as a new atom under the owning feature. `minor`/`nit` → record as
  accepted risk in the ledger, do not fix inside the review.
- One cycle per wave. Do not re-review the same diff, and do not loop the reviewer against the
  fix; the next wave's review covers the fix.
- Carry the reviewer's `unpinned` list forward: behaviour the diff decided that no test pins.
  Either add a test in the fixing atom or record the choice as intentional.

Why clean context: an implementer's tests encode the implementer's assumptions. Both seam
defects observed in the measured run — an unspecified percentile timestamp and an invented
dual-mode restore — were invisible to author-written tests and would have been caught
immediately by a reader who only saw the diff and the acceptance script.

## Phase 7 — Progress reports

The master reports *as* the feature agent — one report per feature — on any of:

- the feature reaches `DONE`;
- a major chunk lands (≥ half its atoms, or its largest atom);
- stall: two consecutive waves with no merge for that feature, or any `BLOCKED` surviving one
  wave.

Template in `references/ledger-and-reports.md`. Every report carries: intent, status, landed
commits with one-line summaries, high-level implementation notes (design choices, new
interfaces, files), open work, blockers with routing, the review verdict, and — for a delay —
the classified cause (`under-specified brief`, `missing contract`, `merge collision`, `external
dependency`, `failing verification`, `saturation`). State delays plainly; no blame, no padding,
no false ETA.

At run end, one summary: features delivered/partial/blocked, commits landed, reverted merges,
review findings by severity, measured width vs serial estimate, and the scratch path holding the
ledger.

## Phase 8 — Cleanup

Remove only worktrees whose branch is merged (`worktree_remove`), delete merged branches with
`-D` once the cherry-pick is verified in `git log`, keep unmerged ones and say so. Discard
hedged branches that were not chosen. Leave `/tmp/opencode/parallel/<run>/` in place as the
audit trail. Never delete operator changes in the canonical checkout.

## Master discipline

- Do not write feature code while two or more executors are in flight. Allowed exceptions:
  merge conflict resolution, skeleton and contract changes, one-line verification fixes.
- Do not run the full suite concurrently with Tier B writers in the same checkout.
- Do not dispatch without a brief on disk; the brief is what survives compaction.
- Do not accept a report that lacks a commit, an acceptance result, and a file list.
- Do not invent atoms to keep slots busy. Idle slots are cheaper than collisions.
- Do not let the reviewer edit, and do not let an executor review its own diff.
- Do not push, tag, or touch `var/` or `.trail/` unless the feature is about them. Executors
  never push; only the master pushes, and only after the gate and `trail check` are clean.
- `/root/FundedBetBacktesting/` is read-only upstream data and never appears in a write set;
  `var/` is the only generated state.
