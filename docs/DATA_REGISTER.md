# Data register

This file is generated from [`docs/data_register.json`](data_register.json). Do not edit it by hand: edit the JSON (or use `python scripts/data_register.py add ...`) and then run `python scripts/data_register.py render`.

**Add new data to `docs/data_register.json` before using it; include name, path, source URL, retrieval date, size, license and the exact re-download/rebuild command. Run `python scripts/data_register.py verify` after a restore.**

`data/` is gitignored, so a clone contains no datasets. Every path below must be restored from its source or rebuilt from the recorded command. `verify` reports `MISSING` when a path is absent and `SIZE_DRIFT` when the measured size differs from the registered size by more than 10%.

Registered: **8 datasets** (7 raw, 1 derived), **2.7 GB** on disk at registration.

## Datasets

| Name | Kind | Path | Size | Source | Retrieved | License |
|---|---|---|---:|---|---|---|
| `espn_match_lifecycle` | derived | `var/lifecycle/match_lifecycle.parquet` | 0 B | Derived from ESPN public site/core API keyEvents (kickoff and end-regular-time wallclock) | 2026-11-18 | ESPN terms of service; personal research use only, not redistributable |
| `polymarket_epl_tape` | raw | `../FundedBetBacktesting/data/polymarket/epl/epl_moneyline_tape.parquet` | 173.1 MB | Derived from SII sports trades + market metadata (EPL moneyline) | 2026-11-18 | MIT (upstream SII data); this repo does not redistribute it |
| `polymarket_football_tape` | raw | `../FundedBetBacktesting/data/polymarket/football/football_moneyline_tape.parquet` | 376.5 MB | Derived from SII sports trades + market metadata (Big-4 moneyline) | 2026-11-18 | MIT (upstream SII data); this repo does not redistribute it |
| `polymarket_sii_markets` | raw | `../FundedBetBacktesting/data/polymarket/sii/markets.parquet` | 545.5 MB | SII Polymarket market metadata | 2026-11-18 | MIT (upstream SII metadata); this repo does not redistribute it |
| `polymarket_results_markets` | raw | `../FundedBetBacktesting/data/results/polymarket_markets.parquet` | 1.3 MB | Derived from local SII market metadata + Big-4 results | 2026-11-18 | MIT (upstream SII metadata) |
| `polymarket_orderbooks_compact` | raw | `../FundedBetBacktesting/data/cache/orderbooks_compact` | 1.6 GB | Derived compaction of the Predexon per-token L2 files | 2026-11-18 | Predexon API terms of service; not redistributable |
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
- **Notes:** One row per match: actual_kickoff_utc, actual_end_utc, source=espn_keyevents, precision=observed. Built by scripts/build_match_lifecycle.py; 2026 fixtures use data/events/espn/summary plus events/fixture_map.parquet (1,006/1,007 with both events); pre-2026 is backfilled through the ESPN scoreboard API. Rows without wallclock are omitted and phase is unknown, never a nominal duration. The derived file lives under var/, so a clean clone has no file and size_bytes is 0; run verify after building and re-measure the size if it is recorded.

### `polymarket_epl_tape`

- **Path:** `../FundedBetBacktesting/data/polymarket/epl/epl_moneyline_tape.parquet`
- **Kind:** raw
- **Source:** Derived from SII sports trades + market metadata (EPL moneyline) <https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data>
- **Retrieved:** 2026-11-18
- **Size:** 173.1 MB (173,110,881 bytes)
- **License:** MIT (upstream SII data); this repo does not redistribute it
- **Re-download / rebuild:**

  ```bash
  python scripts/build_bars.py --all --league epl
  ```
- **Notes:** Read-only v1 source. One row per fill with YES-normalised price_yes and USD notional usd_amount; token_amount is contracts. Provenance: SII-WANGZJ/Polymarket_data on HuggingFace, transformed by the sibling repo. See docs/IMPLEMENTATION.md.

### `polymarket_football_tape`

- **Path:** `../FundedBetBacktesting/data/polymarket/football/football_moneyline_tape.parquet`
- **Kind:** raw
- **Source:** Derived from SII sports trades + market metadata (Big-4 moneyline) <https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data>
- **Retrieved:** 2026-11-18
- **Size:** 376.5 MB (376,483,503 bytes)
- **License:** MIT (upstream SII data); this repo does not redistribute it
- **Re-download / rebuild:**

  ```bash
  python scripts/build_bars.py --all --league big4
  ```
- **Notes:** Read-only v1 source for the Big-4 leagues. One row per fill with YES-normalised price_yes and USD notional usd_amount. Provenance: SII-WANGZJ/Polymarket_data on HuggingFace, transformed by the sibling repo. See docs/IMPLEMENTATION.md.

### `polymarket_sii_markets`

- **Path:** `../FundedBetBacktesting/data/polymarket/sii/markets.parquet`
- **Kind:** raw
- **Source:** SII Polymarket market metadata <https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data>
- **Retrieved:** 2026-11-18
- **Size:** 545.5 MB (545,510,579 bytes)
- **License:** MIT (upstream SII metadata); this repo does not redistribute it
- **Re-download / rebuild:**

  ```bash
  restore from the sibling repo; never modified
  ```
- **Notes:** Read-only metadata source: condition_id, slug, token1/token2, end_date and outcome_prices. 4,049,900 rows upstream. This repo uses it for the YES/NO instrument pair and expiry, never writes it. See docs/IMPLEMENTATION.md.

### `polymarket_results_markets`

- **Path:** `../FundedBetBacktesting/data/results/polymarket_markets.parquet`
- **Kind:** raw
- **Source:** Derived from local SII market metadata + Big-4 results <https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data>
- **Retrieved:** 2026-11-18
- **Size:** 1.3 MB (1,313,866 bytes)
- **License:** MIT (upstream SII metadata)
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py; settlement index is derived from this file
  ```
- **Notes:** Derived in the sibling repo from local SII market metadata plus Big-4 results. The result side inherits football-data.co.uk terms (personal use; not redistributable) and the metadata side is MIT. One row per (market_id, selection_index); settlement_value is 0/1. This repo indexes it into var/settlement/ and never rebuilds or edits the source table.

### `polymarket_orderbooks_compact`

- **Path:** `../FundedBetBacktesting/data/cache/orderbooks_compact`
- **Kind:** raw
- **Source:** Derived compaction of the Predexon per-token L2 files <https://api.predexon.com/v2/polymarket/orderbooks>
- **Retrieved:** 2026-11-18
- **Size:** 1.6 GB (1,550,607,921 bytes)
- **License:** Predexon API terms of service; not redistributable
- **Re-download / rebuild:**

  ```bash
  v1 reads coverage only; L2 replay is deferred to v2
  ```
- **Notes:** Predexon L2 snapshots, 2026-01-01..2026-10-21 (17,182,464 rows; no 2026-06/07 partitions because the Big-4 seasons are off). Snapshots carry depth but no queue position. v1 reads coverage and the latest snapshot at or before a decision; tick-by-tick L2 replay is deferred to v2. Read-only; not redistributed.

### `espn_fixture_map`

- **Path:** `../FundedBetBacktesting/data/events/fixture_map.parquet`
- **Kind:** raw
- **Source:** ESPN fixture spine (match_key to event id) <https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard>
- **Retrieved:** 2026-11-18
- **Size:** 32.9 KB (32,882 bytes)
- **License:** ESPN terms of service; personal research use only, not redistributable
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py
  ```
- **Notes:** ESPN fixture spine mapping match_key to event id; used by scripts/build_match_lifecycle.py. ESPN terms of service: personal research use only, not redistributable.

### `espn_summary_cache`

- **Path:** `../FundedBetBacktesting/data/events/espn/summary`
- **Kind:** raw
- **Source:** ESPN match summaries with keyEvents kickoff/end wallclock <https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary>
- **Retrieved:** 2026-11-18
- **Size:** 34.1 MB (34,147,446 bytes)
- **License:** ESPN terms of service; personal research use only, not redistributable
- **Re-download / rebuild:**

  ```bash
  python scripts/build_match_lifecycle.py
  ```
- **Notes:** 2026 ESPN match-summary cache (1,006/1,007 fixtures with both kickoff and end-regular-time wallclocks). Only keyEvents[].wallclock is read for lifecycle; pre-2026 backfill is cached under var/raw/espn. ESPN terms of service: personal research use only, not redistributable.

---

Generated by `scripts/data_register.py render`. Run `python scripts/data_register.py verify` after a restore.
