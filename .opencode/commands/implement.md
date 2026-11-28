---
description: Dispatch one implementer subagent for a scoped task
---

Dispatch exactly one `implementer` subagent for the task below. Do not implement it in this
session; you plan, brief, integrate and verify.

Create a worktree with the worktree tool first and pass the returned directory into the brief.
The child starts with fresh context and cannot see this conversation, so the brief must stand
alone:

- the task, and the single command that proves it (the acceptance check);
- absolute paths in scope, and an explicit out-of-scope list;
- the worktree to write in, and its branch;
- the rules that bind it: `/root/FundedBetBacktesting/` is read-only, timestamps are UTC, no
  invented numbers, run the tests for the touched domain only, and do not push or touch `var/`.

When the child reports back, review its diff, merge, run the touched domain's tests and
`trail check`, then report. If it stops without finishing, say what is missing rather than
implementing it yourself.

$ARGUMENTS
