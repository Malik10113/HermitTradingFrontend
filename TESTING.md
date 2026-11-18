# Testing

How the suite is organized, which tests a change must run, and how to run them.

> The runner (`scripts/test_domains.sh`) is a milestone 0 deliverable. Until it
> lands, run the area you touched directly: `npm test` in `frontend/`, or
> `pytest tests/<domain>` in the backend.

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
before the tests start. It exits 0 (nothing to run), 1 (tests failed) or 2 (bad
usage) — it never silently skips a domain it cannot attribute.

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
- A domain with no tests exits 0 with "nothing to run".

## Conventions

- Unit: Vitest for `frontend`; pytest for the Python domains.
- E2E: Playwright against a built app with a small fixture catalog.
- Golden fixtures: one indicator fixture per indicator, one deterministic
  backtest fixture with a known equity curve and trade count.
- Deterministic: seed anything random. A test that depends on wall-clock time
  is a bug.
- No network access. A test that needs the sibling data uses a checked-in
  fixture or skips with a reason.
- A test may not pass by mocking the thing under test.
- Missing data or a missing browser reports **skipped with a reason**, never a
  silent pass.
