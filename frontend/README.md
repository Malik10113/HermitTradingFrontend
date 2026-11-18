# Hermit Trading — frontend

React 19 + TypeScript + Vite research workspace for prediction-market bars,
NautilusTrader indicator previews and backtests. The design contract lives in
`../docs/BRIEF.md` and the wire contract in `../docs/IMPLEMENTATION.md`.

## Scripts

```bash
npm install
npm run dev        # Vite on http://127.0.0.1:5173, proxies /api to :8787
npm run build      # tsc --noEmit && vite build -> dist/
npm run test       # vitest (add -- --run for one shot)
npm run typecheck  # tsc --noEmit
```

The production build is served by FastAPI at `/`; all requests are same-origin
`/api`. Vite's dev proxy targets `http://127.0.0.1:8787`.

## Boundaries

- The frontend never talks to NautilusTrader, DuckDB or Parquet. It only calls
  `/api` and renders server-computed series.
- Indicator math, bar construction, settlement and lifecycle phases come from
  the backend. A mismatched indicator series is surfaced as a warning, never
  realigned by guessing or padded.
- Missing values render as `not available`. The UI never substitutes zero, a
  nominal match duration, or an inferred phase.
- Gross and net metrics are shown together. Net is always labelled with its
  effective execution profile. Book → Conservative fallbacks are visible on the
  form and on every affected result.
- No profitability claim, no live trading, no order placement.

## Layout

```
src/
  api/          typed fetch client and wire types
  components/   shell, market bar, chart, indicator chips, strategy rail
  components/results/  metrics, equity, trades, costs, diagnostics, holdout, compare
  hooks/        API resource, debounce, run polling/SSE
  lib/          URL state, formatting, validation, chart mapping, config builder
  pages/        Workspace, Data, Runs, Presets
  state/        meta and theme providers
  test/         Vitest setup (network guard, ResizeObserver, fetch mocks)
```

## Tests

`npm test -- --run`. Unit coverage includes URL round-trips and invalid values,
API error parsing, units/formatting, declarative strategy validation, fallback
badges, chart marker/phase mapping, a workspace wiring smoke test and an
accessibility smoke test. `fetch` is guarded in `src/test/setup.ts`; no test
reaches the network.
