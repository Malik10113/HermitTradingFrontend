"""POST /api/portfolio/optimize: completed-run and holdout guards.

Every run here is a real row in a temporary SQLite state file plus stored
``equity.parquet``/``summary.json`` artifacts under a temporary runs directory.
No sibling data is read, no worker is started and no network is used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.api import routes, services
from backend.engine import artifacts
from backend.main import app

YES = "fixture-YES.POLYMARKET"


def _iso(ts_ns: int) -> str:
    return (
        datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _equity(points: list[tuple[int, float]]) -> list[dict[str, object]]:
    """Equity rows on 2026-01-01 + N days, at the UTC day start."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for day, equity_usd in points:
        ts_ns = int((base + timedelta(days=day)).timestamp() * 1_000_000_000)
        rows.append({"ts": _iso(ts_ns), "ts_ns": ts_ns, "equity_usd": equity_usd})
    return rows


def _write_run(
    runs,
    run_id: str,
    *,
    equity: list[dict[str, object]],
    summary: dict[str, object] | None = None,
) -> None:
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    table = pa.table({}) if not equity else pa.Table.from_pylist(equity)
    pq.write_table(table, run_dir / "equity.parquet")
    import json

    (run_dir / "summary.json").write_text(json.dumps(summary or {}, sort_keys=True))


def _create_run(
    run_id: str,
    *,
    status: str = "complete",
    split: dict[str, str] | None = None,
    data_version: str = "sha256:test",
) -> None:
    from backend.jobs import state

    evaluation: dict[str, object] = {}
    if split is not None:
        evaluation["split"] = split
    state.create_run(
        run_id,
        {"strategy": {"instrument_id": YES}, "evaluation": evaluation},
        "sha256:strategy",
        "sha256:run",
        data_version,
        "conservative",
        "conservative",
    )
    if status != "queued":
        state.update_run(run_id, status=status)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    runs = tmp_path / "var" / "runs"
    runs.mkdir(parents=True)
    for name, value in (
        ("VAR_DIR", tmp_path / "var"),
        ("BARS_DIR", tmp_path / "var" / "bars"),
        ("CATALOG_DIR", tmp_path / "var" / "catalog"),
        ("LIFECYCLE_PATH", tmp_path / "var" / "lifecycle/match_lifecycle.parquet"),
        ("SETTLEMENT_PATH", tmp_path / "var" / "settlement/settlement.parquet"),
        ("INDEX_PATH", tmp_path / "var" / "instruments/instruments.parquet"),
        ("RUNS_DIR", runs),
        ("LOGS_DIR", tmp_path / "var" / "logs"),
        ("SIGNAL_CACHE_DIR", tmp_path / "var" / "cache" / "signals"),
        ("APP_DB", tmp_path / "var" / "app.db"),
    ):
        monkeypatch.setattr(config, name, value)
    from backend.jobs import state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "var" / "app.db")
    monkeypatch.setattr(artifacts, "RUNS_DIR", runs)
    monkeypatch.setattr(routes, "RUNS_DIR", runs)
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")
    monkeypatch.setattr(services, "run_data_version", lambda: "sha256:test")
    with TestClient(app) as client:
        yield client, runs


def _two_runs(runs) -> tuple[str, str]:
    first, second = "run-alpha", "run-beta"
    _create_run(first)
    _write_run(
        runs,
        first,
        equity=_equity(
            [(0, 1000.0), (1, 1010.0), (2, 1005.0), (3, 1020.0), (4, 1030.0), (5, 1040.0)]
        ),
        summary={"net_pnl_usd": 40.0},
    )
    _create_run(second)
    _write_run(
        runs,
        second,
        equity=_equity(
            [(0, 500.0), (1, 505.0), (2, 495.0), (3, 510.0), (4, 520.0), (5, 525.0)]
        ),
        summary={"net_pnl_usd": 25.0},
    )
    return first, second


def test_optimize_returns_portfolio_contract(env):
    client, runs = env
    first, second = _two_runs(runs)

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [first, second], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    for key in (
        "weights",
        "kelly_fractions",
        "effective_n",
        "correlation_matrix",
        "metrics",
        "equity_index",
        "members",
    ):
        assert key in payload, key
    assert set(payload["weights"]) == {first, second}
    assert set(payload["kelly_fractions"]) == {first, second}
    assert [member["run_id"] for member in payload["members"]] == [first, second]
    total = sum(payload["weights"].values())
    assert 0.0 < total <= 1.0 + 1e-9
    assert all(0.0 <= weight <= 0.35 + 1e-9 for weight in payload["weights"].values())
    assert payload["equity_index"], "expected a composed equity curve"
    assert set(payload["correlation_matrix"]) == {first, second}
    assert payload["metrics"]["alignment"] in ("daily_returns", "intraday_bar_returns")


def test_optimize_rejects_too_few_run_ids(env):
    client, _runs = env

    response = client.post("/api/portfolio/optimize", json={"run_ids": ["only-one"]})

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "bad_portfolio"
    assert body["details"] == {"count": 1}


def test_optimize_rejects_duplicate_ids_collapsing_below_two(env):
    client, _runs = env

    response = client.post(
        "/api/portfolio/optimize", json={"run_ids": ["same", "same", " "]}
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "bad_portfolio"
    assert response.json()["details"] == {"count": 1}


def test_optimize_rejects_more_than_ten_run_ids(env):
    client, _runs = env

    response = client.post(
        "/api/portfolio/optimize", json={"run_ids": [f"run-{i}" for i in range(11)]}
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "bad_portfolio"
    assert response.json()["details"] == {"count": 11}


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_optimize_rejects_non_complete_run(env, status):
    client, runs = env
    complete, pending = "run-complete", f"run-{status}"
    _create_run(complete)
    _write_run(runs, complete, equity=_equity([(0, 1000.0), (1, 1001.0)]))
    _create_run(pending, status=status)
    _write_run(runs, pending, equity=_equity([(0, 1000.0), (1, 1001.0)]))

    response = client.post(
        "/api/portfolio/optimize", json={"run_ids": [complete, pending]}
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "run_not_complete"
    assert body["details"] == {"run_id": pending, "status": status}


def test_optimize_restricts_unrevealed_holdout_to_in_sample(env):
    client, runs = env
    split = {"train_end": "2026-01-03", "test_start": "2026-01-04"}
    first, second = "run-holdout-a", "run-holdout-b"
    for run_id, summary in (
        (
            first,
            {
                "net_pnl_usd": 999.0,
                "in_sample": {"net_pnl_usd": 42.0},
            },
        ),
        (
            second,
            {
                "net_pnl_usd": 999.0,
                "in_sample": {"net_pnl_usd": 17.0},
            },
        ),
    ):
        _create_run(run_id, split=split)
        _write_run(
            runs,
            run_id,
            equity=_equity(
                [
                    (0, 1000.0),
                    (1, 1010.0),
                    (2, 1005.0),
                    (3, 1200.0),
                    (4, 1300.0),
                    (5, 1400.0),
                ]
            ),
            summary=summary,
        )

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [first, second], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    # The union of the two runs' dates must stop at the in-sample boundary.
    assert payload["equity_index"]
    assert payload["equity_index"][-1]["ts"].startswith("2026-01-03")
    assert payload["metrics"]["days"] == 3
    warnings = " ".join(payload["warnings"])
    assert "unrevealed holdout split" in warnings
    assert "2026-01-03" in warnings
    # The member's net P&L is measured on the same truncated in-sample curve,
    # so the full-run summary numbers cannot leak through the member row.
    members = {member["run_id"]: member for member in payload["members"]}
    assert members[first]["net_pnl_usd"] == pytest.approx(5.0)
    assert members[second]["net_pnl_usd"] == pytest.approx(5.0)


def test_optimize_allows_revealed_holdout(env):
    client, runs = env
    from backend.jobs import state

    first, second = "run-revealed-a", "run-revealed-b"
    split = {"train_end": "2026-01-03", "test_start": "2026-01-04"}
    for run_id in (first, second):
        _create_run(run_id, split=split)
        state.update_run(run_id, holdout_reveals=1)
        _write_run(
            runs,
            run_id,
            equity=_equity([(0, 1000.0), (1, 1010.0), (2, 1005.0), (4, 1030.0)]),
            summary={"net_pnl_usd": 30.0},
        )

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [first, second], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["equity_index"][-1]["ts"].startswith("2026-01-05")
    assert "unrevealed holdout split" not in " ".join(payload["warnings"])


def test_optimize_strips_run_ids_and_reports_run_provenance(env):
    client, runs = env
    first, second = _two_runs(runs)

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [f"  {first} ", f"\t{second}\n"], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    # Whitespace is stripped before dedupe, so both ids resolve to stored runs.
    assert [member["run_id"] for member in payload["members"]] == [first, second]
    for member in payload["members"]:
        assert member["data_version"] == "sha256:test"
        assert member["run_hash"] == "sha256:run"
    # All sleeves share the current data version, so no staleness is claimed.
    assert "differs from the current data version" not in " ".join(payload["warnings"])


def test_optimize_warns_when_run_data_versions_differ(env, monkeypatch):
    client, runs = env
    monkeypatch.setattr(services, "run_data_version", lambda: "sha256:current")
    first, second = "run-old-a", "run-old-b"
    _create_run(first, data_version="sha256:old-a")
    _write_run(
        runs,
        first,
        equity=_equity([(0, 1000.0), (1, 1010.0), (2, 1005.0)]),
    )
    _create_run(second, data_version="sha256:old-b")
    _write_run(
        runs,
        second,
        equity=_equity([(0, 500.0), (1, 505.0), (2, 495.0)]),
    )

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [first, second], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    warnings = " ".join(payload["warnings"])
    assert first in warnings and second in warnings
    assert "differs from the current data version" in warnings
    assert "multiple data versions" in warnings
    assert "sha256:old-a" in warnings and "sha256:old-b" in warnings
    members = {member["run_id"]: member for member in payload["members"]}
    assert members[first]["data_version"] == "sha256:old-a"
    assert members[second]["data_version"] == "sha256:old-b"


def test_optimize_unrevealed_split_fails_closed_on_untimestamped_points(env):
    client, runs = env
    split = {"train_end": "2026-01-03", "test_start": "2026-01-04"}
    first, second = "run-ts-a", "run-ts-b"
    first_equity: list[dict[str, object]] = _equity(
        [(0, 1000.0), (1, 1010.0), (2, 1005.0), (3, 1200.0), (4, 1300.0)]
    )
    # Neither point can be shown to precede train_end; both must be excluded
    # rather than treated as the epoch (inside the in-sample window).
    first_equity.append({"equity_usd": 99_999.0})
    first_equity.append({"ts": "not-a-timestamp", "equity_usd": 88_888.0})
    second_equity: list[dict[str, object]] = _equity(
        [(0, 500.0), (1, 505.0), (2, 495.0), (3, 900.0)]
    )
    second_equity.append({"ts_ns": None, "equity_usd": 77_777.0})

    for run_id, equity in ((first, first_equity), (second, second_equity)):
        _create_run(run_id, split=split)
        _write_run(
            runs,
            run_id,
            equity=equity,
            summary={"net_pnl_usd": 999.0, "in_sample": {"net_pnl_usd": 999.0}},
        )

    response = client.post(
        "/api/portfolio/optimize",
        json={"run_ids": [first, second], "min_periods": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["equity_index"][-1]["ts"].startswith("2026-01-03")
    members = {member["run_id"]: member for member in payload["members"]}
    # The reported P&L is the truncated in-sample curve change, not the
    # post-train_end bounce (or the full-run summary number).
    assert members[first]["net_pnl_usd"] == pytest.approx(5.0)
    assert members[second]["net_pnl_usd"] == pytest.approx(-5.0)


@pytest.mark.parametrize(
    "split",
    [
        {"test_start": "2026-01-04"},
        {"train_end": "not-a-date", "test_start": "2026-01-04"},
    ],
)
def test_optimize_rejects_unrevealed_split_without_valid_train_end(env, split):
    client, runs = env
    first, second = "run-no-train-a", "run-no-train-b"
    for run_id in (first, second):
        _create_run(run_id, split=split)
        _write_run(runs, run_id, equity=_equity([(0, 1000.0), (1, 1010.0)]))

    response = client.post(
        "/api/portfolio/optimize", json={"run_ids": [first, second]}
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "invalid_split"
    assert body["details"] == {"run_id": first}
