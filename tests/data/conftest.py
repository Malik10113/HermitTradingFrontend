"""Synthetic, offline fixtures for the data layer.

Each test gets its own ``tmp_path`` data root and ``var/`` directory; the
configuration paths are monkeypatched so nothing reads or writes the real
sibling repository.  All timestamps are UTC.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend import config


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _write(path: Path, table: pa.Table) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


def _tape_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("condition_id", pa.string()),
            pa.field("timestamp", pa.int64()),
            pa.field("log_index", pa.int64()),
            pa.field("transaction_hash", pa.string()),
            pa.field("price_yes", pa.float64()),
            pa.field("usd_amount", pa.float64()),
            pa.field("token_amount", pa.float64()),
            pa.field("match_key", pa.string()),
            pa.field("leg", pa.string()),
            pa.field("side_yes", pa.int8()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _markets_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("slug", pa.string()),
            pa.field("condition_id", pa.string()),
            pa.field("token1", pa.string()),
            pa.field("token2", pa.string()),
            pa.field("answer1", pa.string()),
            pa.field("answer2", pa.string()),
            pa.field("event_slug", pa.string()),
            pa.field("question", pa.string()),
            pa.field("end_date", pa.timestamp("us", tz="UTC")),
            pa.field("closed", pa.int8()),
            pa.field("updated_at", pa.timestamp("us", tz="UTC")),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _results_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("market_id", pa.string()),
            pa.field("selection_index", pa.int64()),
            pa.field("settlement_value", pa.float64()),
            pa.field("market_type", pa.string()),
            pa.field("league", pa.string()),
            pa.field("event_date", pa.date32()),
            pa.field("home_name", pa.string()),
            pa.field("away_name", pa.string()),
            pa.field("end_date", pa.timestamp("us", tz="UTC")),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _events_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("match_key", pa.string()),
            pa.field("league", pa.string()),
            pa.field("event_date", pa.date32()),
            pa.field("home_name", pa.string()),
            pa.field("away_name", pa.string()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _fixture_map_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("match_key", pa.string()),
            pa.field("league", pa.string()),
            pa.field("match_id", pa.string()),
            pa.field("espn_event_id", pa.string()),
            pa.field("orientation", pa.string()),
            pa.field("kickoff_utc", pa.timestamp("us", tz="UTC")),
            pa.field("result_kickoff_utc", pa.timestamp("us", tz="UTC")),
            pa.field("result_date", pa.date32()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


@dataclass
class Dataset:
    root: Path
    var: Path
    tape: Path
    football_tape: Path
    markets: Path
    results: Path
    events: Path
    fixture_map: Path
    summary_dir: Path

    # Instrument ids for the two synthetic conditions.
    yes_id: str
    no_id: str
    draw_yes_id: str
    tape_only_id: str
    mismatch_id: str


@pytest.fixture()
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dataset:
    root = tmp_path / "data"
    var = tmp_path / "var"
    tape = root / "polymarket/epl/epl_moneyline_tape.parquet"
    football_tape = root / "polymarket/football/football_moneyline_tape.parquet"
    markets = root / "polymarket/sii/markets.parquet"
    results = root / "results/polymarket_markets.parquet"
    events = root / "results/polymarket_events.parquet"
    fixture_map = root / "events/fixture_map.parquet"
    summary_dir = root / "events/espn/summary"

    # condition 1 (home leg): three active minutes out of four
    home_rows = [
        dict(
            condition_id="cond-home",
            timestamp=100 * 60 + 5,
            log_index=0,
            transaction_hash="tx1",
            price_yes=0.40,
            usd_amount=10.0,
            token_amount=25.0,
            match_key="epl-aaa-bbb-2024-10-19",
            leg="home",
            side_yes=1,
        ),
        dict(
            condition_id="cond-home",
            timestamp=100 * 60 + 15,
            log_index=1,
            transaction_hash="tx2",
            price_yes=0.42,
            usd_amount=5.0,
            token_amount=12.0,
            match_key="epl-aaa-bbb-2024-10-19",
            leg="home",
            side_yes=1,
        ),
        dict(
            condition_id="cond-home",
            timestamp=101 * 60 + 1,
            log_index=0,
            transaction_hash="tx3",
            price_yes=0.38,
            usd_amount=7.0,
            token_amount=18.0,
            match_key="epl-aaa-bbb-2024-10-19",
            leg="home",
            side_yes=-1,
        ),
        dict(
            condition_id="cond-home",
            timestamp=103 * 60 + 10,
            log_index=0,
            transaction_hash="tx4",
            price_yes=0.41,
            usd_amount=3.0,
            token_amount=7.0,
            match_key="epl-aaa-bbb-2024-10-19",
            leg="home",
            side_yes=1,
        ),
    ]
    draw_rows = [
        dict(
            condition_id="cond-draw",
            timestamp=200 * 60,
            log_index=0,
            transaction_hash="tx5",
            price_yes=0.55,
            usd_amount=4.0,
            token_amount=8.0,
            match_key="epl-ccc-ddd-2024-10-20",
            leg="draw",
            side_yes=1,
        )
    ]
    tape_only_rows = [
        dict(
            condition_id="cond-tape-only",
            timestamp=300 * 60,
            log_index=0,
            transaction_hash="tx6",
            price_yes=0.50,
            usd_amount=1.0,
            token_amount=2.0,
            match_key="epl-eee-fff-2024-10-21",
            leg="home",
            side_yes=1,
        )
    ]
    mismatch_rows = [
        dict(
            condition_id="cond-mismatch",
            timestamp=400 * 60,
            log_index=0,
            transaction_hash="tx7",
            price_yes=0.60,
            usd_amount=1.0,
            token_amount=2.0,
            match_key="epl-ggg-hhh-2024-10-22",
            leg="home",
            side_yes=1,
        )
    ]
    _write(tape, _tape_table(home_rows + draw_rows + tape_only_rows + mismatch_rows))
    # The football tape repeats the EPL conditions verbatim, exactly as the real
    # tapes overlap: the index must count each condition from one chosen tape.
    _write(football_tape, _tape_table(home_rows))

    market_rows = [
        dict(
            id="1",
            slug="epl-aaa-bbb-2024-10-19-aaa",
            condition_id="cond-home",
            token1="token-yes-home",
            token2="token-no-home",
            answer1="Yes",
            answer2="No",
            event_slug="epl-aaa-bbb-2024-10-19",
            question="Will Aaa FC win on 2024-10-19?",
            end_date=utc("2024-10-19T16:30:00Z"),
            closed=1,
            updated_at=utc("2024-10-20T09:00:00Z"),
        ),
        # duplicate row for the same condition: the smaller id must win
        dict(
            id="9",
            slug="epl-aaa-bbb-2024-10-19-aaa",
            condition_id="cond-home",
            token1="token-yes-home-dup",
            token2="token-no-home-dup",
            answer1="Yes",
            answer2="No",
            event_slug="epl-aaa-bbb-2024-10-19",
            question="duplicate row",
            end_date=utc("2024-10-19T16:30:00Z"),
            closed=1,
            updated_at=utc("2024-10-20T09:00:00Z"),
        ),
        dict(
            id="2",
            slug="epl-ccc-ddd-2024-10-20-draw",
            condition_id="cond-draw",
            token1="token-yes-draw",
            token2="token-no-draw",
            answer1="Yes",
            answer2="No",
            event_slug="epl-ccc-ddd-2024-10-20",
            question="Will Aaa FC vs. Bbb FC end in a draw?",
            end_date=utc("2024-10-20T16:30:00Z"),
            closed=0,
            updated_at=None,
        ),
        # market without tape rows: excluded by the tape join
        dict(
            id="3",
            slug="epl-iii-jjj-2024-10-23-iii",
            condition_id="cond-market-only",
            token1="token-yes-market",
            token2="token-no-market",
            answer1="Yes",
            answer2="No",
            event_slug="epl-iii-jjj-2024-10-23",
            question="No tape",
            end_date=utc("2024-10-23T16:30:00Z"),
            closed=0,
            updated_at=None,
        ),
        # tape condition whose event is not a moneyline market: excluded
        dict(
            id="4",
            slug="epl-ggg-hhh-2024-10-22-more-markets-home",
            condition_id="cond-mismatch",
            token1="token-yes-mismatch",
            token2="token-no-mismatch",
            answer1="Yes",
            answer2="No",
            event_slug="epl-ggg-hhh-2024-10-22-more-markets",
            question="Not a moneyline leg",
            end_date=utc("2024-10-22T16:30:00Z"),
            closed=0,
            updated_at=None,
        ),
    ]
    _write(markets, _markets_table(market_rows))

    result_rows = [
        dict(
            market_id="cond-home",
            selection_index=0,
            settlement_value=1.0,
            market_type="moneyline",
            league="epl",
            event_date=datetime(2024, 10, 19).date(),
            home_name="Aaa FC",
            away_name="Bbb FC",
            end_date=utc("2024-10-19T16:30:00Z"),
        ),
        dict(
            market_id="cond-home",
            selection_index=1,
            settlement_value=0.0,
            market_type="moneyline",
            league="epl",
            event_date=datetime(2024, 10, 19).date(),
            home_name="Aaa FC",
            away_name="Bbb FC",
            end_date=utc("2024-10-19T16:30:00Z"),
        ),
        # one-sided publication: the NO value must stay unknown
        dict(
            market_id="cond-only-yes",
            selection_index=0,
            settlement_value=1.0,
            market_type="moneyline",
            league="epl",
            event_date=datetime(2024, 10, 24).date(),
            home_name="Only",
            away_name="Yes",
            end_date=utc("2024-10-24T16:30:00Z"),
        ),
    ]
    _write(results, _results_table(result_rows))

    event_rows = [
        dict(
            match_key="epl-aaa-bbb-2024-10-19",
            league="epl",
            event_date=datetime(2024, 10, 19).date(),
            home_name="Aaa FC",
            away_name="Bbb FC",
        ),
        dict(
            match_key="epl-ccc-ddd-2024-10-20",
            league="epl",
            event_date=datetime(2024, 10, 20).date(),
            home_name="Ccc FC",
            away_name="Ddd FC",
        ),
    ]
    _write(events, _events_table(event_rows))

    fixture_rows = [
        dict(
            match_key="epl-aaa-bbb-2024-10-19",
            league="epl",
            match_id="m1",
            espn_event_id="111",
            orientation="normal",
            kickoff_utc=utc("2024-10-19T14:00:00Z"),
            result_kickoff_utc=utc("2024-10-19T14:00:00Z"),
            result_date=datetime(2024, 10, 19).date(),
        ),
        dict(
            match_key="epl-incomplete-2024-10-21",
            league="epl",
            match_id="m2",
            espn_event_id="222",
            orientation="normal",
            kickoff_utc=utc("2024-10-21T14:00:00Z"),
            result_kickoff_utc=utc("2024-10-21T14:00:00Z"),
            result_date=datetime(2024, 10, 21).date(),
        ),
        dict(
            match_key="epl-missing-2024-10-22",
            league="epl",
            match_id="m3",
            espn_event_id="333",
            orientation="normal",
            kickoff_utc=utc("2024-10-22T14:00:00Z"),
            result_kickoff_utc=utc("2024-10-22T14:00:00Z"),
            result_date=datetime(2024, 10, 22).date(),
        ),
    ]
    _write(fixture_map, _fixture_map_table(fixture_rows))

    summary_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(summary_dir / "epl_111.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "keyEvents": [
                    {
                        "type": {"type": "kickoff"},
                        "wallclock": "2024-10-19T14:00:00Z",
                    },
                    {
                        "type": {"type": "goal"},
                        "wallclock": "2024-10-19T14:31:00Z",
                    },
                    {
                        "type": {"type": "end-regular-time"},
                        "wallclock": "2024-10-19T15:50:00Z",
                    },
                ]
            },
            handle,
        )
    # incomplete: kickoff only, no end-regular-time
    with gzip.open(summary_dir / "epl_222.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "keyEvents": [
                    {"type": {"type": "kickoff"}, "wallclock": "2024-10-21T14:05:00Z"}
                ]
            },
            handle,
        )

    monkeypatch.setattr(config, "DATA_ROOT", root)
    monkeypatch.setattr(config, "VAR_DIR", var)
    monkeypatch.setattr(config, "EPL_TAPE", tape)
    monkeypatch.setattr(config, "FOOTBALL_TAPE", football_tape)
    monkeypatch.setattr(config, "MARKETS", markets)
    monkeypatch.setattr(config, "POLYMARKET_MARKETS", results)
    monkeypatch.setattr(config, "POLYMARKET_EVENTS", events)
    monkeypatch.setattr(config, "FIXTURE_MAP", fixture_map)
    monkeypatch.setattr(config, "ESPN_SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(config, "BARS_DIR", var / "bars")
    monkeypatch.setattr(config, "CATALOG_DIR", var / "catalog")
    monkeypatch.setattr(config, "LIFECYCLE_PATH", var / "lifecycle/match_lifecycle.parquet")
    monkeypatch.setattr(config, "SETTLEMENT_PATH", var / "settlement/settlement.parquet")
    monkeypatch.setattr(config, "INDEX_PATH", var / "instruments/instruments.parquet")
    monkeypatch.setattr(config, "DATA_VERSIONS_PATH", var / "data_versions.json")
    # Generated state and read-only siblings must never resolve to the real
    # checkout, even when a test forgets to set them itself.
    monkeypatch.setattr(config, "MATCH_EVENTS", root / "events/match_events.parquet")
    monkeypatch.setattr(config, "BOOKS_DIR", root / "cache/orderbooks_compact")
    monkeypatch.setattr(config, "RUNS_DIR", var / "runs")
    monkeypatch.setattr(config, "APP_DB", var / "app.db")
    monkeypatch.setattr(config, "SIGNAL_CACHE_DIR", var / "cache/signals")
    monkeypatch.setattr(config, "LOGS_DIR", var / "logs")

    return Dataset(
        root=root,
        var=var,
        tape=tape,
        football_tape=football_tape,
        markets=markets,
        results=results,
        events=events,
        fixture_map=fixture_map,
        summary_dir=summary_dir,
        yes_id="epl-aaa-bbb-2024-10-19-aaa-YES.POLYMARKET",
        no_id="epl-aaa-bbb-2024-10-19-aaa-NO.POLYMARKET",
        draw_yes_id="epl-ccc-ddd-2024-10-20-draw-YES.POLYMARKET",
        tape_only_id="epl-eee-fff-2024-10-21-eee-YES.POLYMARKET",
        mismatch_id="epl-ggg-hhh-2024-10-22-more-markets-home-YES.POLYMARKET",
    )
