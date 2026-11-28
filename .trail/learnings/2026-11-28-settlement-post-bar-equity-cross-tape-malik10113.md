---
title: "Settlement post-bar equity, cross-tape dedupe, three-valued not, and preflight sizing"
author: Malik10113
date: 2026-11-28
branch: master
id: l-db9e2d5e
scope: repo
status: active
src: agent
touches:
  - backend/engine/analytics.py
  - backend/engine/rule_strategy.py
  - backend/rules/schema.py
  - backend/data/instruments.py
  - backend/api/services.py
  - frontend/src/components/StrategyRail.tsx
  - frontend/src/api/client.ts
  - backend/data/versions.py
---

## Decided
- `backend/engine/analytics.py` (`build_equity`, `reconstruct_trades`, `analyse`): post-bar settlement timestamps are appended to `equity_curve` when a lot remains open past the last bar, so `metrics.net_pnl_usd` (`final_equity - starting_cash`) agrees with closed-trade net P&L on hold-to-expiry runs. Partial exits deduct `entry_fee_share` from `lot.entry_fee_usd` so entry fees are never double-allocated. Missing `settled_ts_ns` leaves the position `unsettled` instead of imputing `entry_ts_ns`.
- `backend/engine/rule_strategy.py` & `backend/rules/schema.py`: `evaluate_condition` uses three-valued logic (`True | False | None`) internally so `{"not": ...}` returns `False` (never `True`) during indicator warm-up or when lifecycle operands are `None`. `SplitConfig` allows `(test_start - train_end).days == embargo_days`, matching the brief's 1-day embargo example, and `runner.py` excludes trades entered during `(train_end, test_start)` from `in_sample`.
- `backend/data/instruments.py`: `_condition_rows` picks one deterministic `tape_source` per `condition_id` (`min(tape_source)`) before counting fills so EPL conditions duplicated in `football_moneyline_tape.parquet` and `epl_moneyline_tape.parquet` are not 2x counted.
- `backend/api/services.py` & `frontend/src/components/StrategyRail.tsx`: `indicator_preview` computes/caches over the full instrument bar series and slices to `[from, to]` so cache entries without range keys never collide across date windows. `preflight` returns the pre-run `sizing` preview (`stake_usd`, `contracts`, `max_loss_usd`, `max_payout_usd`, `max_profit_usd`) without importing `nautilus_trader` in `backend/api/` or inventing an expiry timestamp.

## Tried and ruled out
- Falling back to `import.meta.env.VITE_APP_TOKEN` in `frontend/src/api/client.ts`: Vite inlines `VITE_*` variables into the public `/assets` bundle, which is served without authentication. `getAppToken` reads runtime storage (`localStorage`) only.
- Bumping `VERSION_SCHEMA` to `relpath-2` in `backend/data/versions.py`: alters the cache lookup key even when all datasets exist on disk, which would needlessly orphan existing production runs.

## Watch out
- In `frontend/src/components/StrategyRail.tsx`, `warnings` must be declared above `config = useMemo(...)` and included with `acknowledged` in its dependency list; declaring `warnings` below causes a temporal-dead-zone `ReferenceError` as soon as the user acknowledges warnings and edits any field.
