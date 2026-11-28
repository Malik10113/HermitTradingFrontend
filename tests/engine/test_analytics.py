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


def test_trade_row_reports_the_fallback_fill():
    """A Book fill that fell back must be visible on the trade row."""
    entry = _fill(0, "BUY", 0.40)
    entry.execution_source = "conservative_fallback"
    entry.fallback_reason = "book_snapshot_stale"
    exit_fill = _fill(100, "SELL", 0.60)
    exit_fill.execution_source = "book"
    trades, _ = reconstruct_trades([entry, exit_fill], _settle(), YES)
    trade = trades[0]
    assert trade["entry_execution_source"] == "conservative_fallback"
    assert trade["entry_fallback_reason"] == "book_snapshot_stale"
    assert trade["exit_execution_source"] == "book"
    assert trade["execution_source"] == "conservative_fallback"
    assert trade["fallback_reason"] == "book_snapshot_stale"


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


def test_trade_row_derives_slippage_from_reference_and_fill_prices():
    fills = [
        _fill(0, "BUY", 0.41, qty=10.0, ref=0.40),
        _fill(100, "SELL", 0.59, qty=10.0, ref=0.60),
    ]
    trades, _ = reconstruct_trades(fills, _settle(), YES)
    trade = trades[0]
    assert trade["entry_reference_price"] == 0.40
    assert trade["exit_reference_price"] == 0.60
    assert trade["entry_slippage_usd"] == pytest.approx(0.10)
    assert trade["exit_slippage_usd"] == pytest.approx(0.10)
    assert trade["slippage_usd"] == pytest.approx(0.20)


def test_trade_row_leaves_slippage_unknown_without_a_reference():
    fills = [_fill(0, "BUY", 0.41), _fill(100, "SELL", 0.59)]
    trades, _ = reconstruct_trades(fills, _settle(), YES)
    trade = trades[0]
    assert trade["entry_slippage_usd"] is None
    assert trade["exit_slippage_usd"] is None
    assert trade["slippage_usd"] is None


def test_settlement_has_no_exit_fill_provenance():
    fills = [_fill(0, "BUY", 0.30, fee=0.2)]
    trades, positions = reconstruct_trades(fills, _settle(1.0, 0.0), YES)
    trade = trades[0]
    assert trade["exit_reason"] == "settlement"
    assert trade["exit_execution_source"] is None
    assert trade["exit_fallback_reason"] is None
    assert trade["slippage_usd"] is None
    assert positions[0]["side"] == "long"
    assert positions[0]["intent"] == "open_long"


def test_partial_exits_allocate_the_entry_fee_once():
    """Two closes of one lot share the entry fee; it is never charged twice."""
    fills = [
        _fill(0, "BUY", 0.40, qty=10.0, fee=1.0),
        _fill(100, "SELL", 0.50, qty=4.0, fee=0.2),
        _fill(200, "SELL", 0.60, qty=6.0, fee=0.3),
    ]
    trades, positions = reconstruct_trades(fills, {}, YES)
    assert positions == []
    assert len(trades) == 2
    first, second = trades
    # 4 of 10 contracts: 0.4 of the 1.0 entry fee, plus its own exit fee.
    assert first["quantity"] == pytest.approx(4.0)
    assert first["fees_usd"] == pytest.approx(0.4 + 0.2)
    assert first["gross_pnl_usd"] == pytest.approx(0.4)
    assert first["net_pnl_usd"] == pytest.approx(0.4 - 0.6)
    # The remaining 6 contracts carry the rest of the entry fee.
    assert second["quantity"] == pytest.approx(6.0)
    assert second["fees_usd"] == pytest.approx(0.6 + 0.3)
    assert second["gross_pnl_usd"] == pytest.approx(1.2)
    assert second["net_pnl_usd"] == pytest.approx(1.2 - 0.9)
    # Nothing invented: total fees equal what entry + both exits charged.
    assert sum(t["fees_usd"] for t in trades) == pytest.approx(1.0 + 0.2 + 0.3)


def test_partial_exits_never_exceed_the_entry_fee_paid():
    """The allocated entry-fee shares always sum back to the paid fee."""
    fills = [
        _fill(0, "BUY", 0.45, qty=9.0, fee=0.9),
        _fill(10, "SELL", 0.50, qty=2.0, fee=0.0),
        _fill(20, "SELL", 0.55, qty=3.0, fee=0.0),
        _fill(30, "SELL", 0.60, qty=4.0, fee=0.0),
    ]
    trades, positions = reconstruct_trades(fills, {}, YES)
    assert positions == []
    assert sum(t["quantity"] for t in trades) == pytest.approx(9.0)
    allocated = sum(t["fees_usd"] for t in trades)
    assert allocated == pytest.approx(0.9)
    # Net of the paid entry fee, every share is gross minus its own fees.
    for t in trades:
        assert t["net_pnl_usd"] == pytest.approx(t["gross_pnl_usd"] - t["fees_usd"])


def test_settlement_without_a_timestamp_is_unsettled_not_imputed():
    """No recorded settlement time means unknown, never the entry time."""
    fills = [_fill(0, "BUY", 0.30, fee=0.1)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, None, "fixture")}
    trades, positions = reconstruct_trades(fills, settlements, YES)
    assert trades == []
    assert len(positions) == 1
    position = positions[0]
    assert position["status"] == "unsettled"
    assert "settled_ts" not in position
    assert "settlement_value" not in position
    # The only timestamp on the row is the entry that really happened.
    assert position["entry_ts_ns"] == 0


def test_default_settle_ts_is_used_when_the_record_has_none():
    """The runner's recorded expiry is a real timestamp, so it may settle."""
    fills = [_fill(0, "BUY", 0.30)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, None, "markets_end_date")}
    trades, positions = reconstruct_trades(fills, settlements, YES, {YES: 500})
    assert len(trades) == 1
    assert trades[0]["exit_ts_ns"] == 500
    assert trades[0]["exit_price"] == 1.0
    assert positions[0]["status"] == "settled"
    assert positions[0]["settled_ts"] is not None


def test_incomplete_settlement_values_are_unsettled():
    """A half-known settlement is unknown: no value is imputed."""
    fills = [_fill(0, "BUY", 0.30)]
    settlements = {CID: SettlementRecord(CID, 1.0, None, 500, "fixture")}
    trades, positions = reconstruct_trades(fills, settlements, YES)
    assert trades == []
    assert positions[0]["status"] == "unsettled"


def test_unsettled_position_keeps_direction_and_entry_provenance():
    entry = _fill(0, "BUY", 0.30)
    entry.execution_source = "conservative_fallback"
    entry.fallback_reason = "book_snapshot_stale"
    trades, positions = reconstruct_trades([entry], {}, YES)
    assert trades == []
    assert positions[0]["status"] == "unsettled"
    assert positions[0]["side"] == "long"
    assert positions[0]["intent"] == "open_long"
    assert positions[0]["entry_execution_source"] == "conservative_fallback"
    assert positions[0]["entry_fallback_reason"] == "book_snapshot_stale"


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


def test_equity_settles_a_hold_to_expiry_position_after_the_last_bar():
    settle_ts = 1_000_000_000_000
    bars = [
        {"ts_event_ns": 0, "close": 0.30},
        {"ts_event_ns": 60_000_000_000, "close": 0.40},
        {"ts_event_ns": 120_000_000_000, "close": 0.50},
    ]
    fills = [_fill(0, "BUY", 0.30, fee=0.1)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, settle_ts, "fixture")}
    result = analyse(
        fills, [], settlements, {YES: bars}, YES, 100.0, 1, 0.0, 0.0, 0.0,
        condition_by_instrument={YES: CID},
    )
    curve = result["equity"]
    # The settlement is its own point, after every stored bar.
    assert curve[-1]["ts_ns"] == settle_ts
    assert curve[-2]["ts_ns"] == 120_000_000_000
    # cash = 100 - 10 * 0.30 - 0.10 entry fee ... + 10 * 1.00 settlement.
    assert curve[-1]["cash_usd"] == pytest.approx(106.9)
    assert curve[-1]["position_value_usd"] == 0.0
    assert curve[-1]["equity_usd"] == pytest.approx(106.9)
    # Final equity and the trade ledger now agree.
    trade_net = sum(t["net_pnl_usd"] for t in result["trades"])
    assert trade_net == pytest.approx(6.9)
    assert result["metrics"]["net_pnl_usd"] == pytest.approx(trade_net)
    assert result["metrics"]["final_equity_usd"] == pytest.approx(106.9)


def test_equity_ignores_settlements_without_a_known_timestamp():
    bars = [{"ts_event_ns": 0, "close": 0.30}, {"ts_event_ns": 100, "close": 0.40}]
    fills = [_fill(0, "BUY", 0.30)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, None, "fixture")}
    curve = build_equity(fills, {YES: bars}, 100.0, settlements, YES)
    assert [p["ts_ns"] for p in curve] == [0, 100]
    # Still marked at the last stored close, never at an invented settlement.
    assert curve[-1]["position_value_usd"] == pytest.approx(4.0)


def test_analyse_forwards_default_settle_ts_for_open_lots():
    """A runner-recorded expiry settles a lot whose record has no timestamp."""
    settle_ts = 1_000_000_000_000
    bars = [
        {"ts_event_ns": 0, "close": 0.30},
        {"ts_event_ns": 60_000_000_000, "close": 0.40},
    ]
    fills = [_fill(0, "BUY", 0.30, fee=0.1)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, None, "markets_end_date")}
    result = analyse(
        fills,
        [],
        settlements,
        {YES: bars},
        YES,
        100.0,
        1,
        0.0,
        0.0,
        0.0,
        condition_by_instrument={YES: CID},
        default_settle_ts_ns={YES: settle_ts},
    )
    curve = result["equity"]
    assert result["positions"][0]["status"] == "settled"
    assert result["trades"][0]["exit_reason"] == "settlement"
    assert result["trades"][0]["exit_ts_ns"] == settle_ts
    # The terminal point is the real expiry, not the last bar.
    assert curve[-1]["ts_ns"] == settle_ts
    assert curve[-1]["position_value_usd"] == 0.0
    # cash = 100 - 10 * 0.30 - 0.10 entry fee + 10 * 1.00 settlement.
    assert curve[-1]["cash_usd"] == pytest.approx(106.9)
    assert curve[-1]["equity_usd"] == pytest.approx(106.9)
    assert result["metrics"]["final_equity_usd"] == pytest.approx(106.9)
    assert result["metrics"]["net_pnl_usd"] == pytest.approx(6.9)


def test_rule_exit_before_the_last_bar_adds_no_post_bar_settlement_point():
    """A run flat at expiry ends on the last stored bar, not the settlement."""
    settle_ts = 1_000_000_000_000
    bars = [
        {"ts_event_ns": 0, "close": 0.30},
        {"ts_event_ns": 100, "close": 0.50},
        {"ts_event_ns": 200, "close": 0.60},
    ]
    fills = [_fill(0, "BUY", 0.30), _fill(100, "SELL", 0.50)]
    settlements = {CID: SettlementRecord(CID, 1.0, 0.0, settle_ts, "fixture")}
    result = analyse(
        fills,
        [],
        settlements,
        {YES: bars},
        YES,
        100.0,
        1,
        0.0,
        0.0,
        0.0,
        condition_by_instrument={YES: CID},
    )
    curve = result["equity"]
    assert result["trades"][0]["exit_reason"] == "rule_exit"
    assert [p["ts_ns"] for p in curve] == [0, 100, 200]
    assert settle_ts not in [p["ts_ns"] for p in curve]
    # cash = 100 - 10 * 0.30 + 10 * 0.50, flat after the rule exit.
    assert curve[-1]["position_value_usd"] == 0.0
    assert curve[-1]["equity_usd"] == pytest.approx(102.0)
    assert result["metrics"]["final_equity_usd"] == pytest.approx(102.0)


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
