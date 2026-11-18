---
title: Run trades lack execution source and entry context
author: Malik10113
date: 2026-11-18
branch: feature/ux
id: l-b0ed23a6
scope: repo
status: active
src: agent
touches:
  - backend/api/routes.py
  - frontend/src/components/results/TradeTable.tsx
  - frontend/src/api/normalise.ts
---

GET /api/backtests/{id} trade rows are built from trades.parquet and do not include execution_source, fallback_reason, slippage_usd or entry_context, because those live on per-fill records in the engine rather than in the trade artifact. The inspector therefore shows 'not available' for them instead of inferring a fallback reason or a price. The run detail likewise omits lifecycle, drawdown and open_positions: drawdown is read from equity.parquet drawdown_pct and lifecycle from the instrument detail, otherwise the UI would imply a phase or drawdown the run never stored.
