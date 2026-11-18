---
title: "Run queue cancellation, orphan reaping and SSE hardening"
author: Malik10113
date: 2026-11-18
branch: feature/hardening
id: l-7e187764
scope: repo
status: active
src: agent
touches:
  - backend/jobs/queue.py
  - backend/jobs/state.py
  - backend/api/routes.py
---

## Discovered
- The experiment cancel loop released the queue slot (by killing the running child) before the still-queued siblings were marked cancelled, so each freed slot let a sibling claim and start. Cancellation is now two-phase: mark every non-terminal child first, then stop the running workers (`QueueManager.request_cancel` + `stop`). One test seeds three children at concurrency 1 and asserts exactly one worker ever starts.
- Duplicate `submit` cannot rely on a status read: both queue threads can see `queued` before either runs. The claim is an atomic `UPDATE runs SET status='running' ... WHERE status='queued'` (`state.claim_run`); the worker is spawned only by the thread that won it. `claim_run` also makes the abandoned queued threads of a cancelled experiment harmless.
- Liveness must treat a zombie as dead (`/proc/<pid>/stat` state `Z`). `os.kill(pid, 0)` succeeds for a zombie, so SIGTERM-then-wait looked like a failed kill and burned the whole grace period. `state.terminate_worker` signals the process group (workers start with `start_new_session=True`) and escalates to SIGKILL.
- SSE needs a default-message `data:` frame beside each named `status` event, and the terminal check must run after the status read in the same poll so a run that finished between polls still gets its `end` frame. The loop sleeps 0.5 s between polls; a test counts `state.events` calls to prove it does not busy-loop.
- Queue threads outlive the API fixture: `shutdown()` must join them, or a late insert after the DB path is restored fails with "no such table".
## Why
These were real ordering/race bugs in cancellation and startup reaping, not test artefacts; the tests drive real subprocesses so the signals and timing are exercised end to end.
