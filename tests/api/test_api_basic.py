"""API smoke tests. No network, no sibling data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import routes, services
from backend.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from backend.jobs import state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")
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
