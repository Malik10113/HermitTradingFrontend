# Changelog

Newest first. One entry per discrete change: what changed, why, and what was
measured. Keep entries short enough to scan; link the commit or PR.

## Template

```markdown
## YYYY-MM-DD  <short title>

- **Changed:** <what the diff does, in one or two lines>
- **Why:** <the reason; link the issue, learning or decision>
- **Measured:** <what was run, and the result; "not measured" is a valid answer>
- **Notes:** <anything a reviewer would otherwise have to reconstruct>
```

---

## 2026-11-18  v1 implementation: scaffold, data layer, engine, API, workspace

- **Changed:** Added the v1 application: Python 3.13/uv project, declarative
  rule schema and hashes, data-version hashing, indicator registry and
  Nautilus-backed computation, execution profiles, run analytics/artifacts,
  process-isolated run queue, SQLite state, FastAPI surface and the React
  workspace. Registered the read-only Polymarket and ESPN sources used by the
  code.
- **Why:** implement `docs/BRIEF.md` milestones 0-2 behind one contract
  (`docs/IMPLEMENTATION.md`), keeping the sibling data read-only and every
  number traceable to an artifact.
- **Measured:** domain tests listed in the commit; indicator and analytics
  golden fixtures run offline. Performance targets not yet measured.
- **Notes:** `BinaryOption` sizing must use `instrument.make_qty`; the engine
  silently drops data with a different size precision. The next-bar-open
  convention uses a quote at each bar open, as recorded in the implementation
  notes and trail learning.

---

## 2026-11-18  Record the GitHub remote and push workflow

- **Changed:** `AGENTS.md` names `origin`
  (`Malik10113/HermitTradingFrontend`), makes `master` the mainline, and
  requires pushing each discrete change after tests and `trail check`.
- **Why:** the repo gained a remote; the contract still read as local-only.
- **Measured:** not measured (documentation only).
- **Notes:** `first-pass` tracks the same commit but is not the mainline;
  `var/`, credentials and sibling data stay off the remote.
