---
title: "Run detail carries fill provenance, entry context and position windows"
author: Malik10113
date: 2026-11-18
branch: feature/run-provenance
id: l-cc176e02
scope: repo
status: active
replaces: l-b0ed23a6
src: agent
touches:
  - backend/api/routes.py
  - backend/engine/analytics.py
  - backend/engine/runner.py
  - frontend/src/components/results/TradeTable.tsx
  - frontend/src/components/PriceChart.tsx
---

## Discovered
- `backend/api/routes.py::_trade_rows` now builds the row from the trades artifact without discarding fields: the artifact `side` (long/short) is kept as `direction`, `side` is the actual entry order side BUY, and settlement trades have `close_side: null` because settlement is not a SELL order. `entry_execution_source`, `exit_execution_source` and their fallback reasons come straight from the per-fill records; absent values stay null.
- Per-trade `slippage_usd` is computed in `backend/engine/analytics.py::reconstruct_trades` only when a reference price exists for that leg. A leg without a reference is unknown, never zero, so old artifacts show "not available".
- `entry_context` is the nearest signals artifact row at or before the entry timestamp with the same instrument and entry action (`long_entry`/`short_entry`). `SignalRecord` now persists `indicator_values` copied from the strategy's signal sink, so the inspector has a real snapshot; no matching row means null.
- `positions` is one window per trade artifact row (entry/exit times, token, direction/intent, prices, settlement flag, provenance). `open_positions` is only `positions.parquet` rows with `status: unsettled`, with `exit_ts: null`. The chart shades these API windows instead of deriving bands from trade gaps.
## Why
The earlier learning said these fields could not exist because they lived only in engine fills. That is no longer true: the artifacts and the API carry them, so the UI must not keep showing placeholders for values the run actually recorded.
