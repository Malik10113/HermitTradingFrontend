"""Analytics tests: trade reconstruction, settlement, equity and metrics."""

from __future__ import annotations

import pytest

from backend.engine.analytics import (
    FillRecord,
    SettlementRecord,
    analyse,
    build_equity,
    compute_metrics,
    reconstruct_trades,
)

YES = "m-YES.POLYMARKET"
NO = "m-NO.POLYMARKET"
CID = "0xabc"


def _fill(ts, side, price, qty=10.0, token="YES", iid=YES, fee=0.0, ref=None):
    return FillRecord(
        ts_ns=ts,
        instrument_id=iid,
        condition_id=CID,
        token=token,
        side=side,
        quantity=qty,
        price=price,
        fee_usd=fee,
        reference_price=ref,
    )


def _settle(yes=1.0, no=0.0, ts=1_000_000):
    return {CID: SettlementRecord(CID, yes, no, ts, "fixture")}


def test_round_trip_trade_and_fees():
    fills = [_fill(0, "BUY", 0.40, fee=0.5), _fill(100, "SELL", 0.60, fee=0.5)]
    trades, positions = reconstruct_trades(fills, _settle(), YES)
    assert len(trades) == 1
    t = trades[0]
    assert t["side"] == "long"
    assert t["token"] == "YES"
    assert t["gross_pnl_usd"] == pytest.approx(2.0)
    assert t["fees_usd"] == 1.0
    assert t["net_pnl_usd"] == pytest.approx(1.0)
    assert t["exit_reason"] == "rule_exit"
    assert positions == []


def test_settlement_closes_open_lot_with_real_value():
    fills = [_fill(0, "BUY", 0.30, fee=0.2)]
    trades, positions = reconstruct_trades(fills, _settle(1.0, 0.0), YES)
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_price"] == 1.0
    assert t["gross_pnl_usd"] == pytest.approx(7.0)
    assert t["net_pnl_usd"] == pytest.approx(6.8)
    assert t["exit_reason"] == "settlement"
    assert positions[0]["status"] == "settled"


def test_short_intent_is_buying_the_complement_token():
    fills = [_fill(0, "BUY", 0.30, token="NO", iid=NO)]
    trades, _ = reconstruct_trades(fills, _settle(1.0, 0.0), YES)
    assert trades[0]["side"] == "short"
    assert trades[0]["token"] == "NO"
    assert trades[0]["entry_yes_prob"] == 0.7
    assert trades[0]["exit_yes_prob"] == 1.0
    assert trades[0]["gross_pnl_usd"] == -3.0


def test_unsettled_instrument_is_reported_not_invented():
    fills = [_fill(0, "BUY", 0.30)]
    trades, positions = reconstruct_trades(fills, {}, YES)
    assert trades == []
    assert positions[0]["status"] == "unsettled"


def test_equity_marks_positions_and_includes_cash():
    bars = [
        {"ts_event_ns": 0, "close": 0.40},
        {"ts_event_ns": 100, "close": 0.50},
        {"ts_event_ns": 200, "close": 0.60},
    ]
    fills = [_fill(50, "BUY", 0.40), _fill(150, "SELL", 0.60)]
    curve = build_equity(fills, {YES: bars}, 100.0, _settle(), YES)
    assert curve[0]["equity_usd"] == 100.0
    # after buy: cash 96, 10 contracts marked at .50 = 101
    assert curve[1]["equity_usd"] == 101.0
    # after sell at .60: cash 102, no position
    assert curve[-1]["equity_usd"] == 102.0
    assert curve[-1]["drawdown_pct"] == 0.0


def test_metrics_core_values():
    fills = [_fill(0, "BUY", 0.4), _fill(100, "SELL", 0.6)]
    settlements = _settle()
    result = analyse(
        fills,
        [],
        settlements,
        {YES: [{"ts_event_ns": 0, "close": 0.4}, {"ts_event_ns": 100, "close": 0.6}]},
        YES,
        100.0,
        1,
        0.0,
        0.0,
        0.0,
    )
    m = result["metrics"]
    assert m["trade_count"] == 1
    assert m["win_rate"] == 1.0
    assert m["net_pnl_usd"] == pytest.approx(2.0)
    assert m["gross_pnl_usd"] == pytest.approx(2.0)
    assert m["held_to_settlement_count"] == 0
    assert result["diagnostics"]["trades_completed"] == 1
    assert result["phase_breakdown"]["unknown"]["trade_count"] == 1


def test_frictionless_costs_are_zero():
    fills = [_fill(0, "BUY", 0.4, ref=0.4), _fill(100, "SELL", 0.6, ref=0.6)]
    result = analyse(fills, [], _settle(), {YES: []}, YES, 100.0, 1, 0.0, 0.0, 0.0)
    assert result["costs"]["fees_usd"] == 0.0
    assert result["costs"]["slippage_usd"] == 0.0
    assert result["costs"]["total_cost_usd"] == 0.0


def test_conservative_slippage_is_measured_from_reference():
    fills = [
        _fill(0, "BUY", 0.41, ref=0.40),
        _fill(100, "SELL", 0.59, ref=0.60),
    ]
    result = analyse(fills, [], _settle(), {YES: []}, YES, 100.0, 1, 0.0, 10.0, 0.001)
    assert round(result["costs"]["slippage_usd"], 10) == 0.2
