"""Settlement index.

``results/polymarket_markets.parquet`` has one row per ``(market_id,
selection_index)`` where ``market_id`` is the Polymarket ``condition_id`` and
``selection_index`` 0 is the YES token, 1 the NO token.  The values are indexed
verbatim into ``var/settlement/settlement.parquet``; nothing is inferred.

When a side is absent from the source the stored value is ``None`` ("not
available"), never a complement or a guess.  ``settled_ts_utc`` comes from
``markets.parquet.updated_at`` only when the market is closed; it is not a
verified settlement timestamp otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from backend import config
from backend.data import sources, versions
from backend.data._parquet import read_metadata, write_table

SETTLEMENT_SCHEMA = pa.schema(
    [
        pa.field("condition_id", pa.string(), nullable=False),
        pa.field("yes_settlement", pa.float64()),
        pa.field("no_settlement", pa.float64()),
        pa.field("settled_ts_utc", pa.timestamp("us", tz="UTC")),
        pa.field("source", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True)
class Settlement:
    condition_id: str
    yes_settlement: float | None
    no_settlement: float | None
    settled_ts_utc: datetime | None
    source: str

    def value_for(self, token_index: int) -> float | None:
        if token_index == 0:
            return self.yes_settlement
        if token_index == 1:
            return self.no_settlement
        raise ValueError(f"token_index must be 0 or 1, got {token_index!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "yes_settlement": self.yes_settlement,
            "no_settlement": self.no_settlement,
            "settled_ts_utc": self.settled_ts_utc,
            "source": self.source,
        }


def _path(out_path: Path | None) -> Path:
    return Path(out_path) if out_path is not None else Path(config.SETTLEMENT_PATH)


def build_settlement(out_path: Path | None = None) -> dict[str, Any]:
    """Build the settlement index from the read-only results tables."""
    results_path = sources.require_file("polymarket_results_markets")
    markets_path = sources.require_file("polymarket_markets")

    con = duckdb.connect()
    try:
        query = """
            WITH published AS (
                SELECT
                    market_id AS condition_id,
                    max(CASE WHEN selection_index = 0 THEN settlement_value END) AS yes_settlement,
                    max(CASE WHEN selection_index = 1 THEN settlement_value END) AS no_settlement
                FROM read_parquet(?)
                WHERE selection_index IN (0, 1)
                GROUP BY 1
            ),
            market_meta AS (
                SELECT condition_id, closed, updated_at
                FROM (
                    SELECT
                        condition_id,
                        closed,
                        updated_at,
                        row_number() OVER (
                            PARTITION BY condition_id ORDER BY id, updated_at
                        ) AS rn
                    FROM read_parquet(?)
                    WHERE condition_id IN (SELECT condition_id FROM published)
                )
                WHERE rn = 1
            )
            SELECT
                p.condition_id,
                p.yes_settlement,
                p.no_settlement,
                CASE WHEN m.closed THEN timezone('UTC', m.updated_at) END AS settled_ts_utc,
                'polymarket_markets' AS source
            FROM published AS p
            LEFT JOIN market_meta AS m USING (condition_id)
            ORDER BY p.condition_id
        """
        table = con.sql(query, params=[str(results_path), str(markets_path)]).to_arrow_table()
    finally:
        con.close()

    table = table.cast(SETTLEMENT_SCHEMA)
    version = versions.settlement_version()
    destination = _path(out_path)
    write_table(
        table,
        destination,
        {
            "settlement_version": version,
            "builder_version": versions.SETTLEMENT_BUILDER_VERSION,
            "source": sources.relative_to_root(results_path),
            "metadata_source": sources.relative_to_root(markets_path),
            "row_count": table.num_rows,
        },
    )
    return {
        "path": destination,
        "rows": table.num_rows,
        "settlement_version": version,
    }


def load_table(path: Path | None = None) -> pa.Table:
    destination = _path(path)
    if not destination.exists():
        build_settlement(destination)
    return pq.read_table(destination)


def load_settlement(path: Path | None = None) -> dict[str, Settlement]:
    """All known settlements keyed by ``condition_id``."""
    table = load_table(path)
    out: dict[str, Settlement] = {}
    for row in table.to_pylist():
        settlement = Settlement(
            condition_id=row["condition_id"],
            yes_settlement=row["yes_settlement"],
            no_settlement=row["no_settlement"],
            settled_ts_utc=row["settled_ts_utc"],
            source=row["source"],
        )
        out[settlement.condition_id] = settlement
    return out


def get_settlement(condition_id: str, path: Path | None = None) -> Settlement | None:
    """Return the published settlement row or ``None`` when absent."""
    return load_settlement(path).get(condition_id)


def settlement_version(path: Path | None = None) -> str:
    """Version recorded on the cached artifact (empty when never built)."""
    return read_metadata(_path(path)).get("settlement_version", "")
