---
title: Market status and audit fixes
author: Malik10113
date: 2026-11-28
branch: master
id: l-82002c29
scope: repo
status: active
src: agent
touches:
  - backend/data/lifecycle.py
  - backend/engine/runner.py
  - frontend/src/components/PriceChart.tsx
  - scripts/build_catalog.py
  - scripts/test_domains.sh
---

The rule operand market_status was effectively absent: lifecycle.market_status_at raised NotImplementedError, while the runner hard-coded null even for known lifecycle rows. Per the user's clarification, use observed match end as the transition from open to closed, and the recorded settlement timestamp as the transition from closed to settled; without a verified lifecycle row return null. Add boundary tests at both timestamps and a runner mapping test. Other audit regressions fixed in the same review: chart rebuild signatures must compare all candle OHLC and volume values (length/time/close-sum checks collide), catalog replacement must restore the existing catalog when promotion fails, and test-domain skips must be surfaced as blocked with a nonzero status rather than reported as a successful full run.
