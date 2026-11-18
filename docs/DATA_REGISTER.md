# Data register

This file is generated from [`docs/data_register.json`](data_register.json). Do not edit it by hand: edit the JSON (or use `python scripts/data_register.py add ...`) and then run `python scripts/data_register.py render`.

**Add new data to `docs/data_register.json` before using it; include name, path, source URL, retrieval date, size, license and the exact re-download/rebuild command. Run `python scripts/data_register.py verify` after a restore.**

`data/` is gitignored, so a clone contains no datasets. Every path below must be restored from its source or rebuilt from the recorded command. `verify` reports `MISSING` when a path is absent and `SIZE_DRIFT` when the measured size differs from the registered size by more than 10%.

Registered: **8 datasets** (7 raw, 1 derived), **2.7 GB** on disk at registration.

## Datasets

| Name | Kind | Path | Size | Source | Retrieved | License |
|---|---|---|---:|---|---|---|
| `espn_match_lifecycle` | derived | `var/lifecycle/match_lifecycle.parquet` | 0 B | Derived from ESPN public site/core API keyEvents (kickoff and end-regular-time wallclock) | 2026-11-18 | ESPN terms of service; personal research use only, not redistributable |
| `polymarket_epl_tape` | raw | `../FundedBetBacktesting/data/polymarket/epl/epl_moneyline_tape.parquet` | 173.1 MB | Derived from SII sports trades + market metadata (EPL moneyline) | 2026-11-18 | MIT (upstream SII data) |
| `polymarket_football_tape` | raw | `../FundedBetBacktesting/data/polymarket/football/football_moneyline_tape.parquet` | 376.5 MB | Derived from SII sports trades + market metadata (Big-4 moneyline) | 2026-11-18 | MIT (upstream SII data) |
| `polymarket_sii_markets` | raw | `../FundedBetBacktesting/data/polymarket/sii/markets.parquet` | 545.5 MB | SII Polymarket market metadata | 2026-11-18 | MIT (upstream SII metadata) |
| `polymarket_results_markets` | raw | `../FundedBetBacktesting/data/results/polymarket_markets.parquet` | 1.3 MB | Derived from local SII market metadata + Big-4 results | 2026-11-18 | MIT (upstream SII metadata) |
| `polymarket_orderbooks_compact` | raw | `../FundedBetBacktesting/data/cache/orderbooks_compact` | 1.6 GB | Derived compaction of the Predexon per-token L2 files | 2026-11-18 | inherits Predexon API terms |
| `espn_fixture_map` | raw | `../FundedBetBacktesting/data/events/fixture_map.parquet` | 32.9 KB | ESPN fixture spine (match_key to event id) | 2026-11-18 | ESPN terms of service; personal research use only, not redistributable |
| `espn_summary_cache` | raw | `../FundedBetBacktesting/data/events/espn/summary` | 34.1 MB | ESPN match summaries with keyEvents kickoff/end wallclock | 2026-11-18 | ESPN terms of service; personal research use only, not redistributable |

## Re-download and rebuild notes

### `espn_match_lifecycle`

- **Path:** `var/lifecycle/match_lifecycle.parquet`
- **Kind:** derived
- **Source:** Derived from ESPN public site/core API keyEvents (kickoff and end-regular-time wallclock) <https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary>
- **Retrieved:** 2026-11-18
- **Size:** 0 B (0 bytes)
- **License:** ESPN terms of service; personal research use only, not redistributable
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py
  ```
- **Notes:** One row per match: actual_kickoff_utc, actual_end_utc, source=espn_keyevents, precision=observed. 2026 fixtures use the existing data/events/espn cache and fixture map (1,006/1,007 with both events); pre-2026 is backfilled via the ESPN scoreboard API. Rows without wallclock are omitted and phase is unknown, never a nominal duration. Raw ESPN summaries are registered upstream as events_espn_raw; pre-2026 backfill will cache under var/raw/espn. Not built yet: the derived path is absent until the lifecycle builder lands.

### `polymarket_epl_tape`

- **Path:** `../FundedBetBacktesting/data/polymarket/epl/epl_moneyline_tape.parquet`
- **Kind:** raw
- **Source:** Derived from SII sports trades + market metadata (EPL moneyline)
- **Retrieved:** 2026-11-18
- **Size:** 173.1 MB (173,110,881 bytes)
- **License:** MIT (upstream SII data)
- **Re-download / rebuild:**

  ```bash
  python scripts/build_bars.py --all --league epl
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `polymarket_football_tape`

- **Path:** `../FundedBetBacktesting/data/polymarket/football/football_moneyline_tape.parquet`
- **Kind:** raw
- **Source:** Derived from SII sports trades + market metadata (Big-4 moneyline)
- **Retrieved:** 2026-11-18
- **Size:** 376.5 MB (376,483,503 bytes)
- **License:** MIT (upstream SII data)
- **Re-download / rebuild:**

  ```bash
  python scripts/build_bars.py --all --league big4
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `polymarket_sii_markets`

- **Path:** `../FundedBetBacktesting/data/polymarket/sii/markets.parquet`
- **Kind:** raw
- **Source:** SII Polymarket market metadata
- **Retrieved:** 2026-11-18
- **Size:** 545.5 MB (545,510,579 bytes)
- **License:** MIT (upstream SII metadata)
- **Re-download / rebuild:**

  ```bash
  restore from the sibling repo; never modified
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `polymarket_results_markets`

- **Path:** `../FundedBetBacktesting/data/results/polymarket_markets.parquet`
- **Kind:** raw
- **Source:** Derived from local SII market metadata + Big-4 results
- **Retrieved:** 2026-11-18
- **Size:** 1.3 MB (1,313,866 bytes)
- **License:** MIT (upstream SII metadata)
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py; settlement index is derived from this file
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `polymarket_orderbooks_compact`

- **Path:** `../FundedBetBacktesting/data/cache/orderbooks_compact`
- **Kind:** raw
- **Source:** Derived compaction of the Predexon per-token L2 files
- **Retrieved:** 2026-11-18
- **Size:** 1.6 GB (1,550,607,921 bytes)
- **License:** inherits Predexon API terms
- **Re-download / rebuild:**

  ```bash
  v1 reads coverage only; L2 replay is deferred to v2
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `espn_fixture_map`

- **Path:** `../FundedBetBacktesting/data/events/fixture_map.parquet`
- **Kind:** raw
- **Source:** ESPN fixture spine (match_key to event id)
- **Retrieved:** 2026-11-18
- **Size:** 32.9 KB (32,882 bytes)
- **License:** ESPN terms of service; personal research use only, not redistributable
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

### `espn_summary_cache`

- **Path:** `../FundedBetBacktesting/data/events/espn/summary`
- **Kind:** raw
- **Source:** ESPN match summaries with keyEvents kickoff/end wallclock
- **Retrieved:** 2026-11-18
- **Size:** 34.1 MB (34,147,446 bytes)
- **License:** ESPN terms of service; personal research use only, not redistributable
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py
  ```
- **Notes:** Read-only v1 source; see docs/IMPLEMENTATION.md.

---

Generated by `scripts/data_register.py render`. Run `python scripts/data_register.py verify` after a restore.
