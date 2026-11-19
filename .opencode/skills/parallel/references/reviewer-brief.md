# Reviewer brief template

One reviewer per wave, dispatched after that wave's integrations are green. The reviewer
sees only the diff and the acceptance script — never the executor briefs, reports, or
ledger. That asymmetry is the point: an implementer's tests encode the implementer's
assumptions.

Write to `/tmp/opencode/parallel/<run>/briefs/<wave>-review.md`, then send:

```text
Read /tmp/opencode/parallel/<run>/briefs/<wave>-review.md and review exactly that diff.
Return the REVIEW block. Do not edit, merge, or create files.
```

## Template

```markdown
# Review <run>/<wave> — <feature intent, one line>

You are reviewing a diff you did not write, with no prior context. Read the code, reason
backwards from it, and report what is wrong or unproven.

## Scope
- Repository: <canonical checkout path>
- Diff: `git diff <BASE>..<HEAD> -- <paths>` (also `git log --oneline <BASE>..<HEAD>`)
- Frozen acceptance script: <path> — read it, do not run anything that mutates state
- Files in scope: <paths>

## What to look for, in this order
1. Correctness: does the diff do what the intent says, in every branch and at every boundary
   (empty input, zero, negative, duplicate, unicode, very large, concurrent)?
2. Contract drift: signatures, return shapes, error types, exit codes, wire formats that do
   not match the acceptance script or each other.
3. Unspecified behaviour: a choice the diff makes that no test pins (default values,
   ordering, timestamps, partial failure). Name it; do not assume the choice is wrong.
4. Isolation: writes outside the declared scope, shared mutable state, temp files, ports,
   databases.
5. Tests: are the assertions strong enough to fail on a plausible wrong implementation?

## Rules
- Do not edit, format, commit, or create files. Your output is a message, nothing else.
- Do not run the full suite or mutate the database; reading and `git diff/show/log` only.
- Cite `path:line` for every finding. No finding without a line.
- Severity: `blocker` (wrong result, data loss, broken contract), `major` (edge case or
  contract drift a user will hit), `minor` (fragile but correct), `nit` (style).
- If you cannot tell whether something is wrong, say so and name the missing test.

## REVIEW
REVIEW <run>/<wave> <APPROVE|BLOCK>
blockers: <count> — <path:line summary, one per line>
major: <count> — <path:line summary>
minor: <count> — <path:line summary>
unpinned: <behaviour the diff decides that no test pins; one per line, or "none">
tests: <are the assertions strong enough? one line>
```

## What the master does with it

- `blocker`/`major` → new atom under the owning feature, with the finding as the acceptance
  check. One cycle only; the fix is reviewed by the next wave, not by re-running this one.
- `minor`/`nit` → ledger as accepted risk; do not expand scope inside the review.
- `unpinned` → either add a test in the fixing atom or record the choice as intentional. An
  unpinned decision that survives two waves becomes a de facto contract; write it down.
- `BLOCK` with no blocker is a reviewer defect: log it and drop the verdict.
