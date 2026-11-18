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

## 2026-11-18  Record the GitHub remote and push workflow

- **Changed:** `AGENTS.md` names `origin`
  (`Malik10113/HermitTradingFrontend`), makes `master` the mainline, and
  requires pushing each discrete change after tests and `trail check`.
- **Why:** the repo gained a remote; the contract still read as local-only.
- **Measured:** not measured (documentation only).
- **Notes:** `first-pass` tracks the same commit but is not the mainline;
  `var/`, credentials and sibling data stay off the remote.
