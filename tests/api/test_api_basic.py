"""API smoke tests. No network, no sibling data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pyarrow as pa
import pyarrow.parquet as pq

from backend import config
from backend.api import routes, services
from backend.data.instruments import INDEX_SCHEMA
from backend.main import app


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Keep the API tests off the real sibling data and ``var/``.

    A one-row index means a lookup miss is a genuine 404 without triggering a
    rebuild that would read the read-only source tree.
    """
    var = tmp_path / "var"
    index = var / "instruments/instruments.parquet"
    index.parent.mkdir(parents=True)
    row = {
        "instrument_id": "fixture-other-2024-10-19-other-YES.POLYMARKET",
        "condition_id": "0xfixture",
        "token_symbol": "YES",
        "token_index": 0,
        "market_id": "1",
        "slug": "fixture-other-2024-10-19-other",
        "question": "Fixture vs Other",
        "league": "epl",
        "match_key": "fixture-other-2024-10-19",
        "event_date": None,
        "home_name": "Fixture",
        "away_name": "Other",
        "selection": "home",
        "price_precision": 3,
        "end_date_utc": None,
        "settlement_value": None,
        "first_ts_utc": None,
        "last_ts_utc": None,
        "trade_count": 0,
        "tape_source": "fixture",
    }
    table = pa.Table.from_pylist([row], schema=INDEX_SCHEMA)
    pq.write_table(table, index)
    for name, value in (
        ("VAR_DIR", var),
        ("BARS_DIR", var / "bars"),
        ("CATALOG_DIR", var / "catalog"),
        ("INDEX_PATH", index),
        ("LIFECYCLE_PATH", var / "lifecycle/match_lifecycle.parquet"),
        ("SETTLEMENT_PATH", var / "settlement/settlement.parquet"),
        ("DATA_VERSIONS_PATH", var / "data_versions.json"),
        ("RUNS_DIR", var / "runs"),
        ("LOGS_DIR", var / "logs"),
    ):
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(services, "run_data_version", lambda: "sha256:test")
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from backend.jobs import state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    with TestClient(app) as c:
        yield c


def test_health_and_meta(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data_version"] == "sha256:test"
    assert "nautilus_version" in body
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/indicators").json()["indicators"]


def test_error_shape_for_unknown_instrument(client):
    r = client.get("/api/instruments/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert body["message"]
    assert "details" in body


def test_unknown_api_route_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["code"] in ("not_found", "http_error")


def test_body_validation_uses_the_stable_error_shape(client):
    r = client.post(
        "/api/backtests",
        json={
            "strategy": {
                "instrument_id": "x-YES.POLYMARKET",
                "bar_type": "1m",
                "direction": "sideways",
            },
            "evaluation": {"date_range": {"from": "2026-01-01", "to": "2026-02-01"}},
        },
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"]
    assert any(issue["path"] == "strategy.direction" for issue in body["details"])


def test_unknown_builtin_param_is_422_not_500(client):
    r = client.post(
        "/api/backtests",
        json={
            "strategy": {
                "instrument_id": "x-YES.POLYMARKET",
                "bar_type": "1m",
                "builtin_id": "sma_trend",
                "builtin_params": {"fast_period": 10, "slow_period": 30},
                "direction": "long",
            },
            "evaluation": {"date_range": {"from": "2026-01-01", "to": "2026-02-01"}},
        },
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "bad_builtin_params"
    assert body["details"]["path"] == "strategy.builtin_params"


def test_unknown_indicator_preview_instrument_is_404(client):
    r = client.post(
        "/api/indicators/preview",
        json={
            "instrument_id": "does-not-exist-YES.POLYMARKET",
            "bar_type": "1m",
            "indicators": [{"instance_id": "ema20", "type": "ema", "params": {"period": 20}}],
        },
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_instrument_serialization_strips_polars_types():
    row = {
        "instrument_id": "x-YES.POLYMARKET",
        "condition_id": "0x1",
        "market_slug": "x",
        "token_symbol": "YES",
        "event_date": "2026-01-01",
        "first_ts_utc": "2026-01-01T00:00:00Z",
        "end_date_utc": "2026-01-02T00:00:00Z",
    }
    out = services.serialize_instrument(row)
    assert out["instrument_id"] == "x-YES.POLYMARKET"
    assert out["token"] == "YES"
    assert out["event_date"].startswith("2026-01-01")


def test_auth_token_is_optional(monkeypatch, tmp_path):
    from backend.config import SETTINGS
    from backend.jobs import state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")
    object.__setattr__(SETTINGS, "app_token", "secret")
    try:
        with TestClient(app) as c:
            assert c.get("/api/health").status_code == 401
            assert c.get("/api/health", headers={"Authorization": "Bearer secret"}).status_code == 200
    finally:
        object.__setattr__(SETTINGS, "app_token", None)
