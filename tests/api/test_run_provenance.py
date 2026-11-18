"""Run-detail provenance: fill sources, entry context, positions, empty runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.api import routes
from backend.engine import artifacts
from backend.main import app

YES = "fixture-YES.POLYMARKET"
NO = "fixture-NO.POLYMARKET"


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _write_parquet(path, rows):
    table = pa.table({}) if not rows else pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def _write_run(runs, run_id: str, *, trades=(), signals=(), positions=()) -> None:
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(run_dir / "trades.parquet", list(trades))
    _write_parquet(run_dir / "signals.parquet", list(signals))
    _write_parquet(run_dir / "positions.parquet", list(positions))


def _create_run(run_id: str, status: str = "complete") -> None:
    from backend.jobs import state

    state.create_run(
        run_id,
        {"strategy": {"instrument_id": YES}},
        "sha256:strategy",
        "sha256:run",
        "sha256:test",
        "book",
        "conservative",
    )
    if status != "queued":
        state.update_run(run_id, status=status, effective_profile="conservative")


@pytest.fixture()
def run_env(tmp_path, monkeypatch):
    var = tmp_path / "var"
    runs = var / "runs"
    runs.mkdir(parents=True)
    for name, value in (
        ("VAR_DIR", var),
        ("BARS_DIR", var / "bars"),
        ("CATALOG_DIR", var / "catalog"),
        ("LIFECYCLE_PATH", var / "lifecycle/match_lifecycle.parquet"),
        ("SETTLEMENT_PATH", var / "settlement/settlement.parquet"),
        ("INDEX_PATH", var / "instruments/instruments.parquet"),
        ("RUNS_DIR", runs),
        ("LOGS_DIR", var / "logs"),
        ("SIGNAL_CACHE_DIR", var / "cache" / "signals"),
        ("APP_DB", var / "app.db"),
    ):
        monkeypatch.setattr(config, name, value)
    from backend.jobs import state

    monkeypatch.setattr(state, "APP_DB", var / "app.db")
    monkeypatch.setattr(artifacts, "RUNS_DIR", runs)
    monkeypatch.setattr(routes, "RUNS_DIR", runs)
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")
    with TestClient(app) as client:
        yield client, runs


def _closed_trade(**overrides):
    row = {
        "entry_ts": _iso(1_000),
        "entry_ts_ns": 1_000,
        "exit_ts": _iso(2_000),
        "exit_ts_ns": 2_000,
        "instrument_id": YES,
        "token": "YES",
        "side": "long",
        "order_side": "BUY",
        "quantity": 10.0,
        "entry_price": 0.40,
        "exit_price": 0.50,
        "entry_reference_price": 0.39,
        "exit_reference_price": 0.51,
        "entry_yes_prob": 0.40,
        "exit_yes_prob": 0.50,
        "gross_pnl_usd": 1.0,
        "fees_usd": 0.1,
        "net_pnl_usd": 0.9,
        "holding_minutes": 0.000016,
        "exit_reason": "rule_exit",
        "entry_phase": "pre_match",
        "exit_phase": "in_play",
        "execution_source": "conservative_fallback",
        "fallback_reason": "book_snapshot_stale",
        "entry_execution_source": "conservative_fallback",
        "entry_fallback_reason": "book_snapshot_stale",
        "exit_execution_source": "book",
        "exit_fallback_reason": None,
        "slippage_usd": 0.2,
        "entry_slippage_usd": 0.1,
        "exit_slippage_usd": 0.1,
    }
    row.update(overrides)
    return row


def _signal(ts_ns: int, action: str, instrument_id: str, **overrides):
    row = {
        "ts_ns": ts_ns,
        "action": action,
        "instrument_id": instrument_id,
        "token": "YES" if action.startswith("long") else "NO",
        "reference_price": 0.38,
        "reason": "",
        "phase": "pre_match",
        "condition_truths": json.dumps({}),
        "indicator_values": json.dumps({"ema20": {"value": 0.39}}),
    }
    row.update(overrides)
    return row


def test_trade_rows_carry_entry_and_exit_provenance_without_losing_direction(run_env):
    client, runs = run_env
    _create_run("run_provenance")
    _write_run(runs, "run_provenance", trades=[_closed_trade()])

    body = client.get("/api/backtests/run_provenance").json()
    trade = body["trades"][0]
    assert trade["direction"] == "long"
    assert trade["side"] == "BUY"
    assert trade["token"] == "YES"
    assert trade["intent"] == "open_long"
    assert trade["close_side"] == "SELL"
    assert trade["entry_execution_source"] == "conservative_fallback"
    assert trade["entry_fallback_reason"] == "book_snapshot_stale"
    assert trade["exit_execution_source"] == "book"
    assert trade["exit_fallback_reason"] is None
    assert trade["slippage_usd"] == pytest.approx(0.2)
    # The raw direction is preserved rather than overwritten by the order side.
    assert trade["side"] != trade["direction"]


def test_short_direction_is_preserved_with_the_no_token(run_env):
    client, runs = run_env
    _create_run("run_short")
    _write_run(
        runs,
        "run_short",
        trades=[
            _closed_trade(
                instrument_id=NO,
                token="NO",
                side="short",
                entry_price=0.30,
                exit_price=0.20,
                entry_reference_price=0.31,
                exit_reference_price=0.19,
                entry_yes_prob=0.70,
                exit_yes_prob=0.80,
                gross_pnl_usd=-1.0,
                net_pnl_usd=-1.1,
            )
        ],
    )

    trade = client.get("/api/backtests/run_short").json()["trades"][0]
    assert trade["direction"] == "short"
    assert trade["intent"] == "open_short"
    assert trade["side"] == "BUY"
    assert trade["token"] == "NO"


def test_entry_context_uses_nearest_matching_signal_at_or_before_entry(run_env):
    client, runs = run_env
    _create_run("run_context")
    _write_run(
        runs,
        "run_context",
        trades=[_closed_trade()],
        signals=[
            _signal(500, "long_entry", YES, reference_price=0.30),
            _signal(900, "long_entry", YES, reference_price=0.38),
            # Later signal: never used for an earlier entry.
            _signal(1_100, "long_entry", YES, reference_price=0.42),
            # Wrong instrument and wrong action: never used.
            _signal(950, "long_entry", NO, reference_price=0.99),
            _signal(950, "short_entry", YES, reference_price=0.11),
        ],
    )

    context = client.get("/api/backtests/run_context").json()["trades"][0]["entry_context"]
    assert context["signal_ts"] == _iso(900)
    assert context["ts_ns"] == 900
    assert context["action"] == "long_entry"
    assert context["condition"] == "long_entry"
    assert context["reference_price"] == pytest.approx(0.38)
    assert context["indicator_values"] == {"ema20": {"value": 0.39}}


def test_entry_context_is_null_when_the_signals_artifact_has_no_match(run_env):
    client, runs = run_env
    _create_run("run_no_context")
    _write_run(runs, "run_no_context", trades=[_closed_trade()])

    trade = client.get("/api/backtests/run_no_context").json()["trades"][0]
    assert trade["entry_context"] is None


def test_positions_and_open_positions_come_from_artifacts(run_env):
    client, runs = run_env
    _create_run("run_positions")
    _write_run(
        runs,
        "run_positions",
        trades=[
            _closed_trade(),
            _closed_trade(
                entry_ts=_iso(3_000),
                entry_ts_ns=3_000,
                exit_ts=_iso(4_000),
                exit_ts_ns=4_000,
                exit_reason="settlement",
                exit_execution_source=None,
                exit_fallback_reason=None,
                exit_price=1.0,
                exit_reference_price=None,
                slippage_usd=None,
            ),
        ],
        positions=[
            {
                "instrument_id": YES,
                "condition_id": "0xfixture",
                "token": "YES",
                "side": "long",
                "intent": "open_long",
                "quantity": 5.0,
                "entry_price": 0.33,
                "entry_ts": _iso(5_000),
                "entry_ts_ns": 5_000,
                "entry_execution_source": "book",
                "entry_fallback_reason": None,
                "status": "unsettled",
            },
            {
                "instrument_id": YES,
                "condition_id": "0xfixture",
                "token": "YES",
                "side": "long",
                "intent": "open_long",
                "quantity": 7.0,
                "entry_price": 0.31,
                "entry_ts": _iso(6_000),
                "settlement_value": 1.0,
                "settled_ts": _iso(7_000),
                "status": "settled",
            },
        ],
    )

    body = client.get("/api/backtests/run_positions").json()
    positions = body["positions"]
    assert len(positions) == 2
    assert positions[0]["entry_ts"] == _iso(1_000)
    assert positions[0]["exit_ts"] == _iso(2_000)
    assert positions[0]["side"] == "long"
    assert positions[0]["intent"] == "open_long"
    assert positions[0]["settlement"] is False
    assert positions[0]["status"] == "closed"
    assert positions[0]["entry_execution_source"] == "conservative_fallback"
    assert positions[0]["exit_execution_source"] == "book"
    assert positions[1]["settlement"] is True
    assert positions[1]["exit_execution_source"] is None

    open_positions = body["open_positions"]
    assert len(open_positions) == 1
    assert open_positions[0]["entry_ts"] == _iso(5_000)
    assert open_positions[0]["exit_ts"] is None
    assert open_positions[0]["side"] == "long"
    assert open_positions[0]["quantity"] == 5.0
    assert open_positions[0]["status"] == "open"
    assert open_positions[0]["entry_execution_source"] == "book"


def test_zero_trade_run_returns_empty_lists_not_phantom_rows(run_env):
    client, runs = run_env
    _create_run("run_empty")
    _write_run(runs, "run_empty")

    body = client.get("/api/backtests/run_empty").json()
    assert body["trades"] == []
    assert body["positions"] == []
    assert body["open_positions"] == []
