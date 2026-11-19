---
title: "v1 handoff: production/build split, backfill coverage, remaining gaps"
author: Malik10113
date: 2026-11-19
branch: master
id: l-e11099df
scope: repo
status: active
src: agent
touches:
  - docs/CHANGELOG.md
---

## Decided
- Production is a detached checkout at /opt/hermit/prod with HTF_VAR=/opt/hermit/var; the build tree is /root/HermitTradingFrontend with /opt/hermit/var-dev. deploy/promote.sh is the only promotion path and keeps var outside the checkout.
- The v1 implementation is complete on master 98fb081; all feature branches and verify/v1-fixes are merged. Their worktrees are stale and must not be merged again.
- Lifecycle backfill is deterministic and non-fuzzy: 874 of 948 pre-2026 fixtures have observed rows; the missing 74 are 21 unmatched and 53 incomplete. Do not lower matching rigor to raise coverage.
## Why
The next agent needs the deployed paths, data version, merge state and the honest gaps without re-deriving them. Production must keep serving while build changes are tested.
## Watch out
- Tailscale Serve is not enabled; local 127.0.0.1:8787 only until the admin console toggle, then `tailscale serve --bg 8787`.
- Remaining gaps: data register (unregistered polymarket_events, lifecycle size 0), quality gap list, APP_TOKEN browser flow, preset parameter reproducibility, Nautilus import boundary clarification.
- Signal plans persist under var/cache/signals; worker runs reuse them. The key must never include execution/sizing/evaluation.
