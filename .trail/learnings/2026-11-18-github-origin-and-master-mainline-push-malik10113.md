---
title: "GitHub origin and master mainline: push each discrete change"
author: Malik10113
date: 2026-11-18
branch: master
id: l-5ea3e557
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
---

Remote: this repo publishes to GitHub at `origin` = git@github.com:Malik10113/HermitTradingFrontend.git; `master` is the mainline and tracks `origin/master`. `first-pass` points at the same commit but is not the mainline, so do not diverge work onto it by accident.
Working rule: finish a discrete change, run the domain tests, then `git push origin master`; never force-push `master`. Unfinished work stays on a local topic branch.
Not to be pushed: `var/`, credentials, generated artifacts, and anything from the read-only sibling data directory.
Evidence: AGENTS.md §3 states the remote and mainline, §11 states the push/force-push rule, and the decision log records the choice.
