---
title: "Prediction-market indicators, strategy templates, and portfolio optimisation port"
author: Malik10113
date: 2026-11-28
branch: master
id: l-34796467
scope: repo
status: active
src: agent
touches:
  - backend/indicators/registry.py
  - backend/indicators/compute.py
  - tests/indicators/reference.py
  - backend/strategies/builtins.py
  - backend/engine/portfolio.py
  - backend/api/routes.py
  - backend/api/services.py
  - frontend/src/components/results/PortfolioPanel.tsx
---

## Decided
- Added 6 prediction-market & trade-flow indicators to `backend/indicators/registry.py` and `backend/indicators/compute.py` (`logit`, `prob_change`, `prob_vol`, `extreme_dist`, `rolling_volume`, `flow_imbalance`) with reference parity in `tests/indicators/reference.py`.
- Ported 5 prediction-market strategy templates from `FundedBetBacktesting` into `backend/strategies/builtins.py`: `flow_momentum`, `large_trade_follow`, `short_horizon_reversion`, `logit_reversion`, and `prob_vol_breakout` (`CATALOGUE_VERSION = "2"`).
- Ported LCB fractional Kelly (`KellyConfig`, `kelly_stake_fraction`), the 5 weighting rules (`equal`, `inverse_volatility`, `risk_parity`, `exponential`, `shrunk_mean_variance`), and the 3-stage correlation-penalised allocator (`three_stage`) into `backend/engine/portfolio.py`, exposed via `POST /api/portfolio/optimize` (`backend/api/routes.py`, `backend/api/services.py`) and rendered in `frontend/src/components/results/PortfolioPanel.tsx` on `RunsPage.tsx`.

## Tried and ruled out
- Passing `_finite_tail(...)` into `_covariance_matrix` inside `_risk_parity_raw` and `_shrunk_mean_variance_raw` (`backend/engine/portfolio.py`): compacting leading `NaN`s shifts index 0 of a late-starting sleeve to align with an earlier sleeve's start date. Slicing `list(returns_by_id[id][-vol_window:])` preserves index alignment so `_pairwise_covariance` pairs identical calendar dates.

## Watch out
- `POST /api/portfolio/optimize` requires `status == "complete"` for every run and restricts unrevealed holdout runs (`holdout_reveals == 0`) to their in-sample window `< train_end` (failing closed with `422 invalid_split` if `train_end` is missing or malformed).
