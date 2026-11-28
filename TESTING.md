# Testing

How the suite is organized, which tests a change must run, and how to run them.

> The runner has landed. Use `scripts/test_domains.sh` for domain-scoped runs;
> `--changed` prints the mapping it used. Missing prerequisites and domains
> without tests are reported as blocked, not passed; e2e runs only when
> `HTF_E2E=1`.

## The protocol, in one rule

> **A change runs the tests of the domain it touches, plus every domain that
> depends on it. Nothing else.** The full suite is a pre-push / pre-merge ritual,
> not a per-edit tax.

The suite is split into seven **domains**. One script is the only supported
entry point for domain-scoped runs; it encodes the mapping so nobody has to
remember it.

```bash
scripts/test_domains.sh --changed
scripts/test_domains.sh --changed --fast
scripts/test_domains.sh --domain engine
scripts/test_domains.sh --all
```

`--changed` prints the mapping it used, so a surprising selection is visible
before the tests start. Exit codes are 0 (selected checks passed or no changed
paths), 1 (a test failed), and 2 (invalid usage or a selected check is blocked).
The final summary lists passed, blocked and failed domains separately.

## Domains

| Domain | Tests | Owns | Depends on |
|---|---|---|---|
| `frontend` | co-located `frontend/src/**/*.test.ts(x)` | `frontend/src` | — |
| `data` | `tests/data` | `backend/data`, `scripts/build_bars.py`, `scripts/build_catalog.py` | — |
| `indicators` | `tests/indicators` | `backend/indicators` | data |
| `engine` | `tests/engine` | `backend/engine` | data, indicators |
| `api` | `tests/api` | `backend/api`, `backend/main.py` | data, indicators, engine |
| `e2e` | `tests/e2e` | `frontend/src`, `backend` | frontend, api |
| `tooling` | `tests/tooling` | `scripts` | — |

Rules for the runner:

- Map every changed path to the most specific owning glob.
- Add every transitive dependent domain.
- Unknown paths go to `tooling` and are printed, never dropped.
- A selected domain with no tests is blocked; it cannot count as a passing check.

## Conventions

- Unit: Vitest for `frontend`; pytest for the Python domains.
- E2E: Playwright against a built app with real `var/` fixtures. It uses the
  system Google Chrome (`channel: "chrome"`); never download a browser. Build
  the frontend and start the app first:

  ```bash
  cd frontend && npm run build
  uv run uvicorn backend.main:app --host 127.0.0.1 --port 8788
  cd frontend && HTF_E2E=1 HTF_E2E_BASE_URL=http://127.0.0.1:8788 npx playwright test
  ```

  Without a reachable app the spec skips with a reason; it never silently
  passes. The runner marks e2e blocked unless `HTF_E2E=1`, and requires a
  reachable app for the Playwright suite to execute.
- Golden fixtures: one indicator fixture per indicator, one deterministic
  backtest fixture with a known equity curve and trade count, and one fixture per
  execution profile (Frictionless = zero cost, Optimistic/Conservative/Stress =
  exact bps plus minimum absolute, Book = book walk, Book with stale/missing
  snapshot = Conservative fallback).
- Deterministic: seed anything random. A test that depends on wall-clock time
  is a bug.
- No network access. A test that needs the sibling data uses a checked-in
  fixture or skips with a reason.
- A test may not pass by mocking the thing under test.
- Missing data or a missing browser reports **skipped with a reason**, never a
  silent pass.
