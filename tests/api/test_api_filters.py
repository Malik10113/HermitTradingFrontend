"""Instrument/search filters, run-status filtering and metric semantics.

Everything runs offline against a temporary instrument index, SQLite state and
``var/`` tree. No sibling data is read and no network is used.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.api import routes, services
from backend.data.instruments import INDEX_SCHEMA
from backend.main import app


def _row(
    slug: str,
    *,
    league: str,
    event_date: date | None,
    question: str,
    token_index: int = 0,
    first_ts: datetime | None = None,
) -> dict:
    suffix = "YES" if token_index == 0 else "NO"
    match_key = slug.rsplit("-", 4)[0] if event_date else None
    return {
        "instrument_id": f"{slug}-{suffix}.POLYMARKET",
        "condition_id": f"0x{slug}{suffix}",
        "token_symbol": suffix,
        "token_index": token_index,
        "market_id": slug,
        "slug": slug,
        "question": question,
        "league": league,
        "match_key": match_key,
        "event_date": event_date,
        "home_name": question.split(" vs ")[0] if " vs " in question else None,
        "away_name": question.split(" vs ")[1] if " vs " in question else None,
        "selection": "home" if token_index == 0 else "away",
        "price_precision": 3,
        "end_date_utc": None,
        "settlement_value": None,
        "first_ts_utc": first_ts,
        "last_ts_utc": first_ts,
        "trade_count": 10,
        "tape_source": "fixture",
    }


FIXTURE_ROWS = [
    _row(
        "alpha-beta-2024-10-19-alpha",
        league="epl",
        event_date=date(2024, 10, 19),
        question="Alpha vs Beta",
        first_ts=datetime(2024, 10, 19, 10, tzinfo=timezone.utc),
    ),
    _row(
        "alpha-beta-2024-10-19-beta",
        league="epl",
        event_date=date(2024, 10, 19),
        question="Alpha vs Beta",
        token_index=1,
        first_ts=datetime(2024, 10, 19, 10, tzinfo=timezone.utc),
    ),
    _row(
        "gamma-delta-2024-10-20-gamma",
        league="lal",
        event_date=date(2024, 10, 20),
        question="Gamma vs Delta",
        first_ts=datetime(2024, 10, 20, 18, tzinfo=timezone.utc),
    ),
    _row(
        "epsilon-zeta-2024-11-01-epsilon",
        league="epl",
        event_date=date(2024, 11, 1),
        question="Epsilon vs Zeta",
        first_ts=datetime(2024, 11, 1, 12, tzinfo=timezone.utc),
    ),
    _row(
        "unknown-date-market",
        league="epl",
        event_date=None,
        question="Undated vs Market",
    ),
]


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    var = tmp_path / "var"
    index = var / "instruments/instruments.parquet"
    index.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(FIXTURE_ROWS, schema=INDEX_SCHEMA), index
    )
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


# ------------------------------------------------------------------ venue


def test_venue_filter_accepts_polymarket(client):
    r = client.get("/api/instruments", params={"venue": "polymarket", "limit": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(FIXTURE_ROWS)
    assert {item["venue"] for item in body["items"]} == {"polymarket"}


def test_unknown_venue_is_a_stable_422(client):
    r = client.get("/api/instruments", params={"venue": "kalshi"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "bad_venue"
    assert body["details"]["supported"] == ["polymarket"]
    assert body["details"]["field"] == "venue"


# ------------------------------------------------------------- event dates


def test_exact_event_date_filter(client):
    r = client.get("/api/instruments", params={"date": "2024-10-19", "limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {item["event_date"] for item in body["items"]} == {"2024-10-19"}


def test_event_date_range_is_inclusive_and_excludes_unknown_dates(client):
    r = client.get(
        "/api/instruments",
        params={"from": "2024-10-20", "to": "2024-11-01", "limit": 100},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    ids = {item["instrument_id"] for item in body["items"]}
    assert "gamma-delta-2024-10-20-gamma-YES.POLYMARKET" in ids
    assert "epsilon-zeta-2024-11-01-epsilon-YES.POLYMARKET" in ids
    # A row with no event_date cannot match a date filter; this is not "all".
    assert all(item["event_date"] is not None for item in body["items"])


def test_invalid_date_and_ambiguous_filters_are_422(client):
    bad = client.get("/api/instruments", params={"date": "2024-02-31"})
    assert bad.status_code == 422
    assert bad.json()["code"] == "bad_date"

    malformed = client.get("/api/instruments", params={"from": "19-10-2024"})
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "bad_date"

    reversed_range = client.get(
        "/api/instruments", params={"from": "2024-11-02", "to": "2024-10-20"}
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()["code"] == "bad_date_range"

    ambiguous = client.get(
        "/api/instruments", params={"date": "2024-10-19", "from": "2024-10-01"}
    )
    assert ambiguous.status_code == 422
    assert ambiguous.json()["code"] == "ambiguous_date_filter"


# --------------------------------------------------------- paging and search


def test_total_is_the_true_match_count_not_the_page(client):
    first = client.get("/api/instruments", params={"limit": 2, "offset": 0}).json()
    assert len(first["items"]) == 2
    assert first["total"] == len(FIXTURE_ROWS)

    second = client.get("/api/instruments", params={"limit": 2, "offset": 2}).json()
    assert second["total"] == len(FIXTURE_ROWS)
    ids = {item["instrument_id"] for item in first["items"]} | {
        item["instrument_id"] for item in second["items"]
    }
    assert len(ids) == 4


def test_league_and_query_filters_compose(client):
    by_league = client.get(
        "/api/instruments", params={"league": "epl", "limit": 100}
    ).json()
    assert by_league["total"] == 4
    assert {item["league"] for item in by_league["items"]} == {"epl"}

    by_query = client.get(
        "/api/instruments", params={"q": "gamma vs delta", "limit": 100}
    ).json()
    assert by_query["total"] == 1
    assert by_query["items"][0]["instrument_id"].startswith("gamma-delta")

    composed = client.get(
        "/api/instruments",
        params={
            "league": "epl",
            "from": "2024-10-01",
            "to": "2024-10-31",
            "limit": 100,
        },
    ).json()
    assert composed["total"] == 2
    assert {item["event_date"] for item in composed["items"]} == {"2024-10-19"}


def test_search_uses_the_same_filters_and_true_total(client):
    r = client.get(
        "/api/search",
        params={
            "q": "alpha",
            "venue": "polymarket",
            "from": "2024-10-19",
            "to": "2024-10-19",
            "limit": 1,
            "offset": 0,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["limit"] == 1
    assert body["items"][0]["venue"] == "polymarket"

    no_match = client.get("/api/search", params={"q": "alpha", "league": "lal"}).json()
    assert no_match["total"] == 0
    assert no_match["items"] == []


# ------------------------------------------------------------------- status


def _create(client, run_id: str, *, data_version: str = "sha256:test") -> None:
    from backend.jobs import state

    state.create_run(
        run_id,
        {"strategy": {"instrument_id": "x-YES.POLYMARKET"}},
        "sha256:strategy",
        "sha256:run",
        data_version,
        "frictionless",
        "frictionless",
    )


def test_backtests_status_filter_and_true_total(client):
    from backend.jobs import state

    _create(client, "run_queued")
    _create(client, "run_running")
    _create(client, "run_complete")
    _create(client, "run_failed")
    state.update_run("run_running", status="running")
    state.update_run("run_complete", status="complete")
    state.update_run("run_failed", status="failed")

    complete = client.get(
        "/api/backtests", params={"status": "complete", "limit": 1}
    ).json()
    assert complete["status"] == "complete"
    assert complete["total"] == 1
    assert len(complete["items"]) == 1

    failed = client.get("/api/backtests", params={"status": "failed"}).json()
    assert failed["total"] == 1

    everything = client.get("/api/backtests", params={"limit": 2}).json()
    assert everything["total"] == 4
    assert len(everything["items"]) == 2
    assert everything["status"] is None


def test_orphaned_status_follows_the_current_data_version(client):
    from backend.jobs import state

    _create(client, "run_current", data_version="sha256:test")
    _create(client, "run_stale", data_version="sha256:old")
    state.update_run("run_current", status="complete")
    state.update_run("run_stale", status="complete")

    complete = client.get("/api/backtests", params={"status": "complete"}).json()
    assert complete["total"] == 1
    assert complete["items"][0]["status"] == "complete"
    assert complete["items"][0]["run_id"] == "run_current"

    orphaned = client.get("/api/backtests", params={"status": "orphaned"}).json()
    assert orphaned["total"] == 1
    assert orphaned["items"][0]["status"] == "orphaned"
    assert orphaned["items"][0]["run_id"] == "run_stale"


def test_unknown_status_is_a_stable_422(client):
    r = client.get("/api/backtests", params={"status": "finished"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "bad_status"
    assert body["details"]["field"] == "status"
    assert "complete" in body["details"]["allowed"]


# --------------------------------------------------------- metric semantics


def test_run_payload_encodes_gross_and_net_basis(client, monkeypatch):
    from backend.jobs import state

    _create(client, "run_metrics")
    state.update_run("run_metrics", status="complete", manifest_json={"execution": {}})

    summary = {"gross_pnl_usd": 10.0, "fees_usd": 2.0, "net_pnl_usd": 8.0}
    monkeypatch.setattr(
        routes,
        "read_json_artifact",
        lambda run_id, name: summary if name == "summary.json" else {},
    )

    r = client.get("/api/backtests/run_metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    semantics = body["metric_semantics"]
    assert "fees" in semantics["gross_pnl_usd"].lower()
    assert "excluded" in semantics["gross_pnl_usd"].lower()
    assert "less fees" in semantics["net_pnl_usd"].lower()
    assert "embedded" in semantics["slippage_usd"].lower()
    assert body["metrics"]["gross_pnl_usd"] == 10.0
    assert body["metrics"]["net_pnl_usd"] == 8.0


# ------------------------------------------------- indicator preview & sizing


def _preview_bars() -> list[dict]:
    """Ten minute bars: six on 2024-10-19, four on 2024-10-20."""
    bars = []
    for index in range(10):
        day = 19 if index < 6 else 20
        minute = index if index < 6 else index - 6
        stamp = datetime(2024, 10, day, 12, minute, tzinfo=timezone.utc)
        bars.append(
            {
                "instrument_id": "x-YES.POLYMARKET",
                "bar_type": "1m",
                "ts_event_ns": int(stamp.timestamp() * 1_000_000_000),
                "open": 0.50,
                "high": 0.60,
                "low": 0.40,
                "close": 0.50 + index / 100.0,
                "volume": 10.0,
                "trade_count": 1,
            }
        )
    return bars


def _loader_for(bars: list[dict]):
    """A ``load_bars`` stand-in that honours the requested window."""

    def load(instrument_id, alias="1m", start_ns=None, end_ns=None, limit=None):
        rows = [
            bar
            for bar in bars
            if (start_ns is None or bar["ts_event_ns"] >= start_ns)
            and (end_ns is None or bar["ts_event_ns"] < end_ns)
        ]
        if limit is not None:
            rows = rows[:limit]
        return {
            "instrument_id": instrument_id,
            "bar_type": alias,
            "data_version": "sha256:bars",
            "bars": rows,
            "has_more": False,
        }

    return load


def test_indicator_preview_series_match_each_window(client, monkeypatch):
    from backend.data import readmodel
    from backend.indicators.cache import DEFAULT_CACHE

    DEFAULT_CACHE.clear()
    bars = _preview_bars()
    monkeypatch.setattr(services, "ensure_bars", lambda *args, **kwargs: None)
    monkeypatch.setattr(readmodel, "load_bars", _loader_for(bars))

    body = {
        "instrument_id": "x-YES.POLYMARKET",
        "bar_type": "1m",
        "indicators": [{"instance_id": "sma3", "type": "sma", "params": {"period": 3}}],
    }

    first = client.post(
        "/api/indicators/preview",
        json={**body, "from": "2024-10-19", "to": "2024-10-19"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["bars"] == 6
    assert first_body["data_version"] == "sha256:bars"
    assert len(first_body["series"]["sma3"]["value"]) == 6
    assert len(first_body["timestamps"]) == 6

    # A second window on the same instrument/spec must not inherit the first
    # window's series length; the full series is sliced per request.
    second = client.post(
        "/api/indicators/preview",
        json={**body, "from": "2024-10-20", "to": "2024-10-20"},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["bars"] == 4
    assert len(second_body["series"]["sma3"]["value"]) == 4
    assert len(second_body["timestamps"]) == 4
    # The first value is warmed by the six preceding bars, not null.
    assert second_body["series"]["sma3"]["value"][0] is not None


def test_preflight_returns_a_sizing_block(client, monkeypatch):
    monkeypatch.setattr(
        services, "bars", lambda *args, **kwargs: {"bars": [{"close": 0.5}]}
    )
    response = client.post(
        "/api/execution/preflight",
        json={
            "instrument_id": "alpha-beta-2024-10-19-alpha-YES.POLYMARKET",
            "from": "2024-10-19",
            "to": "2024-10-19",
            "execution": {"profile": "frictionless"},
            "risk": {"sizing": {"mode": "fixed_stake", "value": 100.0}},
        },
    )
    assert response.status_code == 200, response.text
    sizing = response.json()["sizing"]
    assert sizing is not None
    assert sizing["reference_price"] == pytest.approx(0.5)
    assert sizing["stake_usd"] == pytest.approx(100.0)
    assert sizing["contracts"] == pytest.approx(200.0)
    assert sizing["max_loss_usd"] == pytest.approx(100.0)
    assert sizing["max_payout_usd"] == pytest.approx(200.0)
    assert sizing["max_profit_usd"] == pytest.approx(100.0)


def test_preflight_forwards_indicator_warmup_to_quality(client, monkeypatch):
    seen = {}

    def fake_quality(instrument_id, start=None, end=None, warmup_bars=None):
        seen["warmup_bars"] = warmup_bars
        return {
            "instrument_id": instrument_id,
            "bar_count": 10,
            "warnings": [
                {
                    "code": "insufficient_warmup",
                    "message": (
                        f"10 bars available but {warmup_bars} are required for "
                        "indicator warm-up."
                    ),
                }
            ],
        }

    monkeypatch.setattr(
        services, "bars", lambda *args, **kwargs: {"bars": [{"close": 0.5}]}
    )
    monkeypatch.setattr(services, "quality", fake_quality)
    response = client.post(
        "/api/execution/preflight",
        json={
            "instrument_id": "alpha-beta-2024-10-19-alpha-YES.POLYMARKET",
            "from": "2024-10-19",
            "to": "2024-10-19",
            "execution": {"profile": "frictionless"},
            "risk": {"sizing": {"mode": "fixed_stake", "value": 100.0}},
            "indicators": [
                {"instance_id": "sma50", "type": "sma", "params": {"period": 50}}
            ],
        },
    )
    assert response.status_code == 200, response.text
    # SMA warm-up is period - 1, derived from the instance (no warmup_bars sent).
    assert seen["warmup_bars"] == 49
    codes = [warning["code"] for warning in response.json()["warnings"]]
    assert "insufficient_warmup" in codes


def test_spa_fallback_cannot_escape_the_dist_directory(tmp_path, monkeypatch):
    from backend import main as main_module

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>htf</title>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    monkeypatch.setattr(main_module, "DIST", dist)

    local_app = main_module.create_app()
    client = TestClient(local_app)
    # The SPA route is live: the index page is served.
    index = client.get("/")
    assert index.status_code == 200 and "htf" in index.text

    for path in ("/..%2fsecret.txt", "/%2e%2e/secret.txt"):
        response = client.get(path)
        assert "TOP-SECRET" not in response.text, f"{path} leaked a file outside dist/"
        assert "htf" in response.text


# ---------------------------------------------------------------------- meta


def test_meta_names_the_markets_dataset_and_computes_catalog_coverage(client):
    body = client.get("/api/meta").json()
    by_name = {item["name"]: item for item in body["datasets"]}
    assert by_name["polymarket_sii_markets"]["path"] == "polymarket/sii/markets.parquet"
    assert "results/polymarket_markets.parquet" in {
        item["path"] for item in body["datasets"]
    }

    catalog = body["catalog"]
    assert catalog["instruments"] == len(FIXTURE_ROWS)
    assert catalog["coverage_from"] == "2024-10-19T10:00:00Z"
    assert catalog["coverage_to"] == "2024-11-01T12:00:00Z"

    # No global book window can be computed here, so none is claimed.
    assert body["book_profile"]["global_coverage_window"] is None
    assert "2026" not in str(body["book_profile"])
