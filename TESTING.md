# Testing

How the suite is organized, which tests a change must run, and how to run them.

> **Framework scaffold.** The protocol below is fixed. The domain table and the
> runner are intentionally empty: fill them in once the frontend stack and test
> runner are chosen, then add `scripts/test_domains.*` for that stack.

## The protocol, in one rule

> **A change runs the tests of the domain it touches, plus every domain that
> depends on it. Nothing else.** The full suite is a pre-push / pre-merge ritual,
> not a per-edit tax.

The suite is split into **domains**. Each domain owns a directory under `tests/`
and a set of source paths. One script is the only supported entry point for
domain-scoped runs; it encodes the mapping so nobody has to remember it.

```bash
# What does my working tree need? (staged, unstaged and untracked files)
<runner> --changed

# Same, but skip slow end-to-end tests
<runner> --changed --fast

# One domain explicitly
<runner> --domain <domain>

# Everything (pre-merge / CI)
<runner> --all
```

`--changed` prints the mapping it used, so a surprising selection is visible
before the tests start. It exits 0 (nothing to run), 1 (tests failed) or 2 (bad
usage) — it never silently skips a domain it cannot attribute.

## Domains

| Domain | Tests | Owns | Depends on |
|---|---|---|---|
| `<domain>` | `tests/<domain>` | `<source globs>` | — |

Rules for filling this in:

- A domain is a *reason to run tests together*, not necessarily a folder that
  mirrors `src/`.
- `Owns` lists source globs, not single files; the runner maps every changed
  path to the most specific glob.
- `Depends on` is transitive: the runner adds every dependent domain.
- New tests go in the directory of the domain whose source they exercise.
- A domain decision is the weakest level reached: a passing unit test does not
  stand in for a passing e2e test.

## Conventions

- Tests must be deterministic; seed anything random.
- No network access in unit tests. Anything that reads the sibling data source
  goes in its own domain and is skipped when the data is absent.
- A test that cannot run (missing data, missing browser) reports **skipped with
  a reason** — never a silent pass.
