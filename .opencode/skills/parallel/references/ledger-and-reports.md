# Ledger and report formats

## Ledger

`/tmp/opencode/parallel/<run>/ledger.json` — the only durable state. Re-read it after every
compaction; never rebuild it from the transcript. Update it on every dispatch, report, merge,
revert, and width change.

```json
{
  "run": "1118-1402",
  "base": "a906b89",
  "skeleton": "a906b89",
  "branch": "master",
  "repo": "/root/HermitTradingFrontend",
  "commands": {
    "task": "scripts/test_domains.sh --domain data --fast",
    "wave": "scripts/test_domains.sh --all"
  },
  "costs": { "c_seconds": 600, "notes": "seeded; replaced after first measured round trip" },
  "dirty_at_start": ["docs/CHANGELOG.md"],
  "waves": [
    { "n": 1, "started": 1731940000, "ended": 1731942100, "merged": 3, "reverted": 0, "t_seconds": 2100 }
  ],
  "features": [
    {
      "id": "F1",
      "intent": "invalidate cache entries on adapter version bump",
      "acceptance": "scripts/test_domains.sh --domain data green and cache tests cover version bump",
      "status": "active",
      "reports_sent": 1,
      "blocked_by": [],
      "tasks": [
        {
          "id": "F1-E1",
          "intent": "add version field to CacheEntry with default",
          "wave": 1,
          "kind": "atom",
          "imports": [],
          "write_set": ["backend/data/sources.py#load_tape"],
          "test_scope": ["data"],
          "depends_on": [],
          "worktree": "/root/.local/share/opencode/worktree/eff35f/1118-1402-f1-e1",
          "branch": "pll/1118-1402/f1-e1",
          "status": "merged",
          "commit": "3f9c1ab",
          "started": 1731940000,
          "ended": 1731940340,
          "check": "python3 -m unittest tests.test_store -v -> OK",
          "blocker": null,
          "notes": "one commit, no deviations"
        }
      ]
    }
  ],
  "merge_queue": ["F1-E2", "F2-E1"],
  "integrations": [
    { "task": "F1-E2", "branch": "pll/1118-1402/f1-e2", "merge": "7c21d90",
      "gate": "task check OK", "seconds": 41 }
  ],
  "reviews": [
    { "wave": 1, "verdict": "BLOCK", "blockers": 1, "major": 0, "minor": 2,
      "unpinned": ["p95 output timestamp"] }
  ],
  "hedges": [
    { "task": "F1-E5", "branches": ["pll/1118-1402/f1-e5", "pll/1118-1402/f1-e5b"],
      "chosen": "pll/1118-1402/f1-e5b", "reason": "smaller diff, same gate" }
  ],
  "reverts": [],
  "width": { "current": 3, "cap": 6, "last_change_shortened_wave": true }
}
```

### Plan file

`plan.tsv` sits beside the ledger and is the dispatch schedule `plan_check.mjs` reads. Five
tab-separated fields, one task per line, `#` comments and blank lines ignored:

```text
# id	wave	kind	imports	write_set
F1-E1	1	atom	-	backend/data/sources.py tests/data/test_sources.py
F1-DOC	1	docs	-	docs/DATA_TRACE.md
F1-E5	2	facade	F1-E1,F1-E2,F1-E3	backend/api/routes.py tests/api/test_routes.py
```

`imports` lists task ids whose modules this task imports, or `-`. `write_set` is
space-separated. The ledger carries the same `wave`, `kind` and `imports` fields, plus the
`test_scope` domains the task runs; if the first three disagree, the plan is wrong and
`plan_check.mjs` is the arbiter.

### Worktree bootstrap record

Every Tier A task carries the resources it was given, so a failing atom can be told apart
from a broken environment:

```json
"bootstrap": { "fixtures": ["var/ (read-only copy)"], "env": [".env.test"],
               "port": 8102, "database": "<worktree>/var/test.sqlite3" }
```

Two live tasks may not share a port or a writable database. If an atom fails only in its
worktree, check the bootstrap before re-briefing.

### Integration log

One line per cherry-pick, in `integrations`. `gate` records the narrow check and whether the
shared-file gate ran. A red pick is recorded with its reset/revert sha, not deleted from the
log: the sequence is what a later bisect needs.

### Review record

One entry per wave, cited in the progress report. `unpinned` items are the reviewer's list of
behaviours the diff decided that no test pins; carry them forward until they are tested or
declared intentional. A review that returns `BLOCK` without at least one blocker is a
reviewer defect.

Status values: `queued`, `dispatched`, `reported`, `merged`, `reverted`, `blocked`,
`parked`, `abandoned`. A task with no `commit` is not done, whatever the report says.

## Executor report

The executor emits this as the tail of its final message; the master copies it into the
ledger and does not paraphrase it.

```text
REPORT F1-E2 DONE 3f9c1ab
files: backend/data/sources.py:41-58
check: pytest tests/data/test_sources.py -q -> passed (34 tests)
deviations: none
blocker: none
risk: CacheEntry is constructed positionally in one adapter fixture
```

`blocker.kind` decides routing: `brief-defect` → same feature, narrower brief, new executor;
`missing-contract` → spine task under the seam owner; `collision` → serialize under the
region owner; `external` → master, one retry then park; `test-failure` → revert and
re-dispatch with the failing check as acceptance.

## Feature progress report

Posted in the session by the master **as** the feature agent, on DONE, on a major chunk, or
on stall. Markdown, no preamble, no closing filler.

```markdown
### F1 — invalidate cache entries on adapter version bump · <DONE|ACTIVE|BLOCKED|PARTIAL>
Landed: 3f9c1ab CacheEntry.version, 8ad2e10 store rejects stale schema, 41c7d0f GC keeps versioned rows
Implementation: version is stored beside the payload and compared at read time; GC now keys on
(version, key) so old rows age out normally. No migration, no schema bump, no adapter changes.
Open: version bump on the GitHub adapter (F1-E3), then the acceptance run.
Blockers: F1-E3 parked on F2-E1 (shared `Document.version` field): routed to F2, spine task queued.
Delay: none — wave 2 finished in 34 min, 3 merges, 0 reverts.
Next: F1-E3 after F2-E1 merges; then `scripts/test_domains.sh --all` as the feature gate.
```

For a stall, replace the last two lines with the classified cause and the recovery step:

```markdown
Delay: under-specified brief (F1-E2 acceptance named no test) — re-briefed narrower, fresh
executor dispatched; wave 2 produced no merge while that resolved.
Next: merge F1-E2 at wave 3; width held at 2 until the queue drains.
```

Cause vocabulary: `under-specified brief`, `missing contract`, `merge collision`,
`external dependency`, `failing verification`, `saturation`. One line each, specific to the
feature; never a general apology.

## Run summary

```text
Run 1118-1402 · base a906b89 · width 3 (cap 6) · waves 3
F1 DONE   4 commits  (3 merged, 1 reverted and re-landed)
F2 ACTIVE 2 commits  (1 blocked on external: registry 503)
Landed 6 commits, 1 revert, 0 open worktrees, ledger at /tmp/opencode/parallel/1118-1402/
Measured: serial estimate 95 min, wall 61 min, speedup 1.6x — width 3 was the knee; width 4 did not shorten wave 3.
```
