from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.engine.runner import _effective_expiration_ns, _lifecycle_values
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    ExecutionConfig,
    RiskConfig,
    RunConfig,
    SizingConfig,
    SplitConfig,
    StrategySection,
)

from .conftest import COMPLEMENT_ID, PRIMARY_ID, SLUG


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def test_match_market_uses_the_observed_end_not_the_scheduled_kickoff():
    row = {"end_date_utc": utc(2026, 5, 10, 15, 30)}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "observed_match_end"
    assert ns == int(lifecycle.actual_end_utc.timestamp() * 1_000_000_000)


def test_market_end_wins_when_it_is_already_later():
    row = {"end_date_utc": utc(2026, 12, 31, 23, 0)}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "market_end_date"
    assert ns == int(row["end_date_utc"].timestamp() * 1_000_000_000)


def test_no_lifecycle_keeps_the_market_expiry():
    row = {"end_date_utc": utc(2026, 5, 10, 15, 30)}

    ns, source = _effective_expiration_ns(row, None)

    assert source == "market_end_date"
    assert ns == int(row["end_date_utc"].timestamp() * 1_000_000_000)


def test_existing_expiration_ns_is_honoured():
    row = {"expiration_ns": 1_800_000_000_000_000_000}
    lifecycle = SimpleNamespace(actual_end_utc=utc(2026, 5, 10, 17, 33, 34))

    ns, source = _effective_expiration_ns(row, lifecycle)

    assert source == "market_end_date"
    assert ns == 1_800_000_000_000_000_000


def test_runner_passes_market_status_to_lifecycle_operands():
    row = SimpleNamespace(
        actual_kickoff_utc=utc(2026, 5, 10, 15, 30),
        actual_end_utc=utc(2026, 5, 10, 17, 0),
    )
    settlement = utc(2026, 5, 10, 17, 5)
    bar_time = utc(2026, 5, 10, 17, 2)

    values = _lifecycle_values(int(bar_time.timestamp() * 1_000_000_000), row, settlement)

    assert values["phase"] == "post_match"
    assert values["market_status"] == "closed"
    assert values["time_to_settlement_minutes"] == 3.0


# ---------------------------------------------------------------------------
# execute_run: bar pagination, settlement validation and split embargo
# ---------------------------------------------------------------------------

TS0 = int(datetime(2024, 10, 1, 12, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
BAR = 60 * 10**9


def _ts(year, month, day, hour=12):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _bar_row(ts, close):
    return {
        "ts_event_ns": ts,
        "ts_init_ns": ts + BAR,
        "open": close,
        "high": close + 0.01,
        "low": close - 0.01,
        "close": close,
        "volume": 100.0,
        "trade_count": 10,
    }


def _instrument_row(instrument_id, outcome, token):
    return {
        "instrument_id": instrument_id,
        "symbol": token,
        "token_symbol": token,
        "outcome": outcome,
        "condition_id": SLUG,
        "match_key": SLUG,
        "slug": SLUG,
        "question": "Bournemouth vs Arsenal",
        "end_date_utc": "2024-10-19T17:00:00Z",
        "price_precision": 3,
    }


def _prepared():
    return {
        "primary": _instrument_row(PRIMARY_ID, "YES", "7543210987654321"),
        "complement": _instrument_row(COMPLEMENT_ID, "NO", "7543210987654322"),
        "data_version": "sha256:test",
        "code_version": "sha256:code",
        "strategy_hash": "sha256:strategy",
        "run_hash": "sha256:run",
        "split_hash": None,
        "quality": None,
        "warnings": [],
        "book_coverage": {"has_coverage": False, "window": {}},
    }


def _config(*, split=None):
    return RunConfig(
        strategy=StrategySection(
            instrument_id=PRIMARY_ID,
            bar_type="1m",
            long_entry={"gt": ["close", 0.45]},
            direction="long",
        ),
        execution=ExecutionConfig(profile="conservative"),
        risk=RiskConfig(sizing=SizingConfig()),
        evaluation=EvaluationConfig(
            date_range=DateRange(start="2024-09-01", end="2026-10-01"),
            split=split,
        ),
    )


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """Stub the data layer and the disk signal store; keep the engine real."""
    from backend.data import lifecycle as lifecycle_module
    from backend.data import settlement as settlement_module
    from backend.engine import runner as runner_module
    from backend.engine import signals as signals_module

    monkeypatch.setattr(runner_module, "_lifecycle_version", lambda: None)
    monkeypatch.setattr(lifecycle_module, "lifecycle_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(settlement_module, "get_settlement", lambda *args, **kwargs: None)
    cache = signals_module.SignalCache(
        store=signals_module.SignalPlanStore(root=tmp_path / "signals")
    )
    monkeypatch.setattr(signals_module, "DEFAULT_SIGNAL_CACHE", cache)
    try:
        yield SimpleNamespace(cache=cache, settlement=settlement_module)
    finally:
        cache.clear(disk=True)


def _stub_loader(monkeypatch, *, primary_pages, complement_pages=None):
    from backend.data import readmodel

    calls = []
    complement_pages = complement_pages or [
        {"bars": [_bar_row(TS0, 0.60)], "has_more": False}
    ]

    def fake_load(instrument_id, alias="1m", start_ns=None, end_ns=None, limit=None):
        calls.append(
            {
                "instrument_id": instrument_id,
                "start_ns": start_ns,
                "end_ns": end_ns,
                "limit": limit,
            }
        )
        queue = primary_pages if instrument_id == PRIMARY_ID else complement_pages
        return queue.pop(0)

    monkeypatch.setattr(readmodel, "load_bars", fake_load)
    return calls


def test_execute_run_paginates_bars_while_has_more(runner_env, monkeypatch):
    from backend.engine.runner import execute_run

    calls = _stub_loader(
        monkeypatch,
        primary_pages=[
            {"bars": [_bar_row(TS0, 0.40), _bar_row(TS0 + BAR, 0.42)], "has_more": True},
            {
                "bars": [_bar_row(TS0 + 2 * BAR, 0.43), _bar_row(TS0 + 3 * BAR, 0.41)],
                "has_more": False,
            },
        ],
    )

    outcome = execute_run(_config().model_dump(mode="json"), _prepared())

    assert outcome.status == "complete"
    primary_calls = [c for c in calls if c["instrument_id"] == PRIMARY_ID]
    assert len(primary_calls) == 2
    # The second page starts immediately after the first page's last bar.
    assert primary_calls[1]["start_ns"] == TS0 + BAR + 1
    assert primary_calls[1]["limit"] == 200_000
    assert "bars: primary=4" in outcome.log


def test_execute_run_ignores_a_settlement_with_no_yes_value(runner_env, monkeypatch):
    from backend.engine import analytics
    from backend.engine.runner import execute_run

    _stub_loader(
        monkeypatch, primary_pages=[{"bars": [_bar_row(TS0, 0.40)], "has_more": False}]
    )
    monkeypatch.setattr(
        runner_env.settlement,
        "get_settlement",
        lambda *args, **kwargs: SimpleNamespace(
            yes_settlement=None, no_settlement=0.0, settled_ts_utc=None
        ),
    )
    captured = {}
    real_analyse = analytics.analyse

    def spy(fills, signals, settlements, bars, *args, **kwargs):
        captured["settlements"] = settlements
        return real_analyse(fills, signals, settlements, bars, *args, **kwargs)

    monkeypatch.setattr(analytics, "analyse", spy)

    outcome = execute_run(_config().model_dump(mode="json"), _prepared())

    assert outcome.status == "complete"
    assert captured["settlements"] == {}


def test_execute_run_excludes_embargo_trades_from_both_slices(runner_env, monkeypatch):
    from backend.engine import analytics
    from backend.engine.runner import execute_run

    _stub_loader(
        monkeypatch, primary_pages=[{"bars": [_bar_row(TS0, 0.40)], "has_more": False}]
    )
    template = {
        "net_pnl_usd": 1.0,
        "fees_usd": 0.0,
        "entry_yes_prob": 0.4,
        "exit_yes_prob": 0.5,
    }
    canned_trades = [
        {**template, "entry_ts_ns": _ts(2024, 10, 2)},  # last in-sample day
        {**template, "entry_ts_ns": _ts(2024, 10, 3)},  # embargo gap
        {**template, "entry_ts_ns": _ts(2024, 10, 5)},  # test_start
    ]
    monkeypatch.setattr(
        analytics,
        "analyse",
        lambda *args, **kwargs: {
            "trades": [dict(trade) for trade in canned_trades],
            "costs": {"fallback_rate": 0.0},
        },
    )
    config = _config(
        split=SplitConfig(train_end="2024-10-02", test_start="2024-10-05", embargo_days=1)
    )

    outcome = execute_run(config.model_dump(mode="json"), _prepared())

    assert outcome.status == "complete"
    result = outcome.result
    assert len(result["trades"]) == 3
    assert result["in_sample"]["trade_count"] == 1
    assert result["out_of_sample"]["trade_count"] == 1
    assert [t["entry_ts_ns"] for t in result["oos_trades"]] == [_ts(2024, 10, 5)]

