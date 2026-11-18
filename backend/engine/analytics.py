"""Deterministic post-run analytics.

The Nautilus engine produces fills and positions; this module turns them into
the artifacts the dashboard reads: round-trip trades, equity, drawdown, costs,
core and prediction-market metrics, phase breakdown and diagnostics.  Nothing
here invents a price: settlement comes from the indexed results table and every
mark comes from a stored bar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


def _utc(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)


def _iso(ts_ns: int | None) -> str | None:
    return _utc(ts_ns).isoformat().replace("+00:00", "Z") if ts_ns is not None else None


@dataclass
class FillRecord:
    ts_ns: int
    instrument_id: str
    condition_id: str
    token: str  # "YES" | "NO"
    side: str  # "BUY" | "SELL"
    quantity: float
    price: float
    fee_usd: float = 0.0
    reference_price: float | None = None
    execution_source: str = "fixed"
    impact_bps: float | None = None
    slippage_bps: float = 0.0
    fallback_reason: str | None = None
    order_id: str | None = None
    signal_id: str | None = None
    phase: str = "unknown"


@dataclass
class SignalRecord:
    ts_ns: int
    action: str
    instrument_id: str
    token: str
    reference_price: float | None
    reason: str = ""
    phase: str = "unknown"
    condition_truths: dict[str, bool] = field(default_factory=dict)


@dataclass
class SettlementRecord:
    condition_id: str
    yes_settlement: float
    no_settlement: float
    settled_ts_ns: int | None
    source: str = "polymarket_markets"


@dataclass
class Lot:
    instrument_id: str
    condition_id: str
    token: str
    quantity: float
    entry_price: float
    entry_ts_ns: int
    entry_fee_usd: float
    entry_ref: float | None
    entry_phase: str
    signal_id: str | None = None
    entry_execution_source: str = "fixed"
    entry_fallback_reason: str | None = None


def _settle_value(settlement: SettlementRecord, token: str) -> float:
    return settlement.yes_settlement if token.upper() == "YES" else settlement.no_settlement


def _yes_equivalent(price: float, token: str) -> float:
    return price if token.upper() == "YES" else 1.0 - price


def reconstruct_trades(
    fills: Iterable[FillRecord],
    settlements: dict[str, SettlementRecord],
    primary_instrument_id: str,
    default_settle_ts_ns: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """FIFO-match BUY/SELL fills and settle open lots.

    Returns ``(trades, positions)``.  A trade is one closed lot; ``side`` is
    the intent relative to the primary token (``long`` buys the primary,
    ``short`` buys the complement) and ``token`` is always the token actually
    traded.
    """
    default_settle_ts_ns = default_settle_ts_ns or {}
    lots: dict[str, list[Lot]] = {}
    trades: list[dict[str, Any]] = []

    def close_lot(lot: Lot, qty: float, exit_price: float, exit_ts: int, fee: float, reason: str, phase: str, exit_source: str = "fixed", exit_fallback_reason: str | None = None) -> None:
        gross = (exit_price - lot.entry_price) * qty
        entry_fee_share = lot.entry_fee_usd * (qty / lot.quantity) if lot.quantity else 0.0
        fees = entry_fee_share + fee
        direction = "long" if lot.instrument_id == primary_instrument_id else "short"
        fallback_reason = exit_fallback_reason or lot.entry_fallback_reason
        execution_source = (
            exit_source
            if exit_fallback_reason
            else (lot.entry_execution_source or exit_source)
        )
        trades.append(
            {
                "entry_ts": _iso(lot.entry_ts_ns),
                "entry_ts_ns": lot.entry_ts_ns,
                "exit_ts": _iso(exit_ts),
                "exit_ts_ns": exit_ts,
                "instrument_id": lot.instrument_id,
                "token": lot.token,
                "side": direction,
                "order_side": "BUY",
                "quantity": qty,
                "entry_price": lot.entry_price,
                "exit_price": exit_price,
                "entry_yes_prob": _yes_equivalent(lot.entry_price, lot.token),
                "exit_yes_prob": _yes_equivalent(exit_price, lot.token),
                "entry_notional_usd": lot.entry_price * qty,
                "max_loss_usd": lot.entry_price * qty,
                "max_payout_usd": qty,
                "max_profit_usd": (1.0 - lot.entry_price) * qty,
                "gross_pnl_usd": gross,
                "fees_usd": fees,
                "net_pnl_usd": gross - fees,
                "holding_minutes": (exit_ts - lot.entry_ts_ns) / 60_000_000_000,
                "exit_reason": reason,
                "entry_phase": lot.entry_phase,
                "exit_phase": phase,
                "signal_id": lot.signal_id,
                "entry_reference_price": lot.entry_ref,
                # Per-fill provenance so a Book fallback is visible on the
                # trade row, not only in the run-level summary.
                "execution_source": execution_source,
                "fallback_reason": fallback_reason,
                "entry_execution_source": lot.entry_execution_source,
                "entry_fallback_reason": lot.entry_fallback_reason,
                "exit_execution_source": exit_source,
                "exit_fallback_reason": exit_fallback_reason,
            }
        )

    for f in sorted(fills, key=lambda x: (x.ts_ns, x.order_id or "")):
        if f.side == "BUY":
            lots.setdefault(f.instrument_id, []).append(
                Lot(
                    instrument_id=f.instrument_id,
                    condition_id=f.condition_id,
                    token=f.token,
                    quantity=f.quantity,
                    entry_price=f.price,
                    entry_ts_ns=f.ts_ns,
                    entry_fee_usd=f.fee_usd,
                    entry_ref=f.reference_price,
                    entry_phase=f.phase,
                    signal_id=f.signal_id,
                    entry_execution_source=f.execution_source,
                    entry_fallback_reason=f.fallback_reason,
                )
            )
        else:
            remaining = f.quantity
            queue = lots.get(f.instrument_id, [])
            while remaining > 1e-12 and queue:
                lot = queue[0]
                take = min(remaining, lot.quantity)
                close_lot(
                    lot,
                    take,
                    f.price,
                    f.ts_ns,
                    f.fee_usd * (take / f.quantity),
                    "rule_exit",
                    f.phase,
                    exit_source=f.execution_source,
                    exit_fallback_reason=f.fallback_reason,
                )
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 1e-12:
                    queue.pop(0)

    # Settle whatever is still open.
    open_positions: list[dict[str, Any]] = []
    for instrument_id, queue in lots.items():
        for lot in queue:
            if lot.quantity <= 1e-12:
                continue
            settlement = settlements.get(lot.condition_id)
            if settlement is None:
                open_positions.append(
                    {
                        "instrument_id": instrument_id,
                        "condition_id": lot.condition_id,
                        "token": lot.token,
                        "quantity": lot.quantity,
                        "entry_price": lot.entry_price,
                        "entry_ts": _iso(lot.entry_ts_ns),
                        "status": "unsettled",
                    }
                )
                continue
            value = _settle_value(settlement, lot.token)
            ts = settlement.settled_ts_ns or default_settle_ts_ns.get(instrument_id) or lot.entry_ts_ns
            close_lot(lot, lot.quantity, value, ts, 0.0, "settlement", "settled")
            open_positions.append(
                {
                    "instrument_id": instrument_id,
                    "condition_id": lot.condition_id,
                    "token": lot.token,
                    "quantity": lot.quantity,
                    "entry_price": lot.entry_price,
                    "settlement_value": value,
                    "settled_ts": _iso(ts),
                    "status": "settled",
                }
            )

    trades.sort(key=lambda t: (t["exit_ts_ns"], t["entry_ts_ns"]))
    return trades, open_positions


def build_equity(
    fills: Iterable[FillRecord],
    bars_by_instrument: dict[str, list[dict[str, Any]]],
    starting_cash_usd: float,
    settlements: dict[str, SettlementRecord],
    primary_instrument_id: str,
    condition_by_instrument: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Cash + marked positions at every stored bar timestamp."""
    fills = sorted(fills, key=lambda x: x.ts_ns)
    # Union of bar timestamps, primary first for determinism.
    timestamps: list[int] = []
    seen: set[int] = set()
    for iid in [primary_instrument_id, *[k for k in bars_by_instrument if k != primary_instrument_id]]:
        for b in bars_by_instrument.get(iid, []):
            ts = int(b["ts_event_ns"])
            if ts not in seen:
                seen.add(ts)
                timestamps.append(ts)
    timestamps.sort()
    if not timestamps and fills:
        timestamps = sorted({f.ts_ns for f in fills})
    if not timestamps:
        return [
            {
                "ts": _iso(0) if False else None,
                "ts_ns": 0,
                "cash_usd": starting_cash_usd,
                "position_value_usd": 0.0,
                "equity_usd": starting_cash_usd,
                "drawdown_pct": 0.0,
            }
        ]

    # Last known close for marking.
    last_close: dict[str, float] = {}
    bars_iter = {iid: iter(bs) for iid, bs in bars_by_instrument.items()}
    next_bar = {iid: next(it, None) for iid, it in bars_iter.items()}
    fi = 0
    qty: dict[str, float] = {}
    cash = float(starting_cash_usd)
    equity_curve: list[dict[str, Any]] = []
    peak = cash
    settled_ts = {
        int(s.settled_ts_ns): s for s in settlements.values() if s.settled_ts_ns is not None
    }
    for ts in timestamps:
        # apply fills at or before this timestamp
        while fi < len(fills) and fills[fi].ts_ns <= ts:
            f = fills[fi]
            fi += 1
            if f.side == "BUY":
                cash -= f.price * f.quantity + f.fee_usd
                qty[f.instrument_id] = qty.get(f.instrument_id, 0.0) + f.quantity
            else:
                cash += f.price * f.quantity - f.fee_usd
                qty[f.instrument_id] = qty.get(f.instrument_id, 0.0) - f.quantity
                if qty[f.instrument_id] <= 1e-12:
                    qty.pop(f.instrument_id, None)
        # settle anything whose settlement timestamp is here
        for iid in list(qty):
            cid = (condition_by_instrument or {}).get(iid, _condition_from_instrument(iid))
            s = settlements.get(cid)
            if s is None or s.settled_ts_ns is None or s.settled_ts_ns > ts:
                continue
            value = _settle_value(s, "YES" if iid.endswith("-YES.POLYMARKET") else "NO")
            cash += qty[iid] * value
            qty.pop(iid, None)
        # advance marks
        for iid, bar in list(next_bar.items()):
            while bar is not None and int(bar["ts_event_ns"]) <= ts:
                last_close[iid] = float(bar["close"])
                bar = next(bars_iter[iid], None)
            next_bar[iid] = bar
        position_value = sum(q * last_close.get(iid, 0.0) for iid, q in qty.items())
        equity = cash + position_value
        peak = max(peak, equity)
        dd = (equity / peak - 1.0) * 100.0 if peak else 0.0
        equity_curve.append(
            {
                "ts": _iso(ts),
                "ts_ns": ts,
                "cash_usd": cash,
                "position_value_usd": position_value,
                "equity_usd": equity,
                "drawdown_pct": dd,
            }
        )
    _ = settled_ts
    return equity_curve


def _condition_from_instrument(instrument_id: str) -> str:
    # The index provides the authoritative mapping; callers can pass a dict.
    return instrument_id


def _daily_equity(curve: list[dict[str, Any]]) -> list[tuple[date, float]]:
    by_day: dict[date, float] = {}
    for p in curve:
        if p.get("ts_ns") is None:
            continue
        d = _utc(int(p["ts_ns"])).date()
        by_day[d] = float(p["equity_usd"])
    if not by_day:
        return []
    start, end = min(by_day), max(by_day)
    out: list[tuple[date, float]] = []
    prev = None
    d = start
    while d <= end:
        value = by_day.get(d, prev)
        if value is None:
            value = next(iter(by_day.values()))
        out.append((d, value))
        prev = value
        d += timedelta(days=1)
    return out


def _sharpe(curve: list[dict[str, Any]]) -> float | None:
    daily = _daily_equity(curve)
    if len(daily) < 3:
        return None
    rets = []
    for (_, a), (_, b) in zip(daily, daily[1:]):
        if a > 0:
            rets.append(b / a - 1.0)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(365.0)


def compute_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    starting_cash_usd: float,
    evaluation_days: int,
    time_in_market_minutes: float,
    fill_count: int,
    signal_count: int,
    reject_count: int,
    total_slippage_usd: float,
) -> dict[str, Any]:
    final_equity = float(equity_curve[-1]["equity_usd"]) if equity_curve else starting_cash_usd
    net = final_equity - starting_cash_usd
    gross = sum(t["gross_pnl_usd"] for t in trades)
    fees = sum(t["fees_usd"] for t in trades)
    wins = [t for t in trades if t["net_pnl_usd"] > 0]
    losses = [t for t in trades if t["net_pnl_usd"] < 0]
    win_rate = len(wins) / len(trades) if trades else None
    avg_win = sum(t["net_pnl_usd"] for t in wins) / len(wins) if wins else None
    avg_loss = sum(t["net_pnl_usd"] for t in losses) / len(losses) if losses else None
    gross_win = sum(t["net_pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    expectancy = sum(t["net_pnl_usd"] for t in trades) / len(trades) if trades else None
    max_dd = min((p["drawdown_pct"] for p in equity_curve), default=0.0) if equity_curve else 0.0
    exposure = time_in_market_minutes / (evaluation_days * 24 * 60) if evaluation_days > 0 else None
    holding = [t["holding_minutes"] for t in trades if t["holding_minutes"] is not None]
    settlement_trades = [t for t in trades if t["exit_reason"] == "settlement"]
    early_exits = [t for t in trades if t["exit_reason"] != "settlement"]
    notional = sum(t["entry_notional_usd"] for t in trades)
    yes_points = [
        t["exit_yes_prob"] - t["entry_yes_prob"]
        for t in trades
        if t["entry_yes_prob"] is not None and t["exit_yes_prob"] is not None
    ]
    metrics = {
        "starting_cash_usd": starting_cash_usd,
        "final_equity_usd": final_equity,
        "net_pnl_usd": net,
        "gross_pnl_usd": gross,
        "fees_usd": fees,
        "slippage_usd": total_slippage_usd,
        "return_pct": (net / starting_cash_usd * 100.0) if starting_cash_usd else None,
        "sharpe": _sharpe(equity_curve),
        "max_drawdown_pct": max_dd,
        "trade_count": len(trades),
        "win_rate": win_rate,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "profit_factor": profit_factor,
        "expectancy_usd": expectancy,
        "avg_holding_minutes": (sum(holding) / len(holding)) if holding else None,
        "exposure_pct": exposure * 100.0 if exposure is not None else None,
        "avg_entry_prob": (sum(t["entry_yes_prob"] for t in trades) / len(trades)) if trades else None,
        "avg_exit_prob": (sum(t["exit_yes_prob"] for t in trades) / len(trades)) if trades else None,
        "avg_yes_points_captured": (sum(yes_points) / len(yes_points)) if yes_points else None,
        "avg_contracts": (sum(t["quantity"] for t in trades) / len(trades)) if trades else None,
        "return_per_capital_deployed_pct": (net / sum(t["max_loss_usd"] for t in trades) * 100.0) if trades and sum(t["max_loss_usd"] for t in trades) > 0 else None,
        "return_per_notional_pct": (net / notional * 100.0) if notional > 0 else None,
        "settlement_pnl_usd": sum(t["net_pnl_usd"] for t in settlement_trades),
        "trading_pnl_usd": sum(t["net_pnl_usd"] for t in early_exits),
        "held_to_settlement_count": len(settlement_trades),
        "early_exit_count": len(early_exits),
        "fill_count": fill_count,
        "signal_count": signal_count,
        "reject_count": reject_count,
        "cost_bps_of_notional": ((fees + total_slippage_usd) / notional * 10_000.0) if notional > 0 else None,
        "gross_return_pct": (gross / starting_cash_usd * 100.0) if starting_cash_usd else None,
        "net_return_pct": (net / starting_cash_usd * 100.0) if starting_cash_usd else None,
    }
    # Presentation aliases used by the workspace; same numbers, explicit units.
    metrics["avg_entry_probability"] = metrics["avg_entry_prob"]
    metrics["avg_exit_probability"] = metrics["avg_exit_prob"]
    metrics["avg_probability_move_pts"] = metrics["avg_yes_points_captured"]
    metrics["avg_holding_period_s"] = (
        metrics["avg_holding_minutes"] * 60.0 if metrics["avg_holding_minutes"] is not None else None
    )
    metrics["gross"] = {
        "gross_pnl_usd": metrics["gross_pnl_usd"],
        "gross_return_pct": metrics["gross_return_pct"],
        "fees_usd": metrics["fees_usd"],
        "slippage_usd": metrics["slippage_usd"],
    }
    metrics["net"] = {
        "net_pnl_usd": metrics["net_pnl_usd"],
        "net_return_pct": metrics["net_return_pct"],
        "trade_count": metrics["trade_count"],
        "win_rate": metrics["win_rate"],
    }
    return metrics


def phase_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    phases = ["pre_match", "in_play", "post_match", "settled", "unknown"]
    out: dict[str, Any] = {}
    for phase in phases:
        subset = [t for t in trades if t.get("entry_phase") == phase]
        out[phase] = {
            "trade_count": len(subset),
            "net_pnl_usd": sum(t["net_pnl_usd"] for t in subset),
            "win_rate": (len([t for t in subset if t["net_pnl_usd"] > 0]) / len(subset)) if subset else None,
            "return_pct": (sum(t["net_pnl_usd"] for t in subset) / sum(t["entry_notional_usd"] for t in subset) * 100.0)
            if subset and sum(t["entry_notional_usd"] for t in subset) > 0
            else None,
        }
    return out


def cost_breakdown(
    fills: list[FillRecord],
    fee_bps: float,
    slippage_bps: float,
    min_abs_slippage: float,
) -> dict[str, Any]:
    fees = sum(f.fee_usd for f in fills)
    slip_usd = 0.0
    spread_usd = 0.0
    impact_usd = 0.0
    for f in fills:
        if f.impact_bps is not None:
            impact_usd += f.price * f.quantity * float(f.impact_bps) / 10_000.0
        if f.reference_price is None:
            continue
        if f.side == "BUY":
            slip_usd += max(f.price - f.reference_price, 0.0) * f.quantity
        else:
            slip_usd += max(f.reference_price - f.price, 0.0) * f.quantity
    fallbacks = [f for f in fills if f.execution_source == "conservative_fallback"]
    notional = sum(f.price * f.quantity for f in fills)
    return {
        "fees_usd": fees,
        "slippage_usd": slip_usd,
        "spread_usd": spread_usd,
        # Informational: the book impact is already inside the fill price and
        # therefore inside slippage_usd; do not add it twice.
        "impact_usd": impact_usd,
        "total_cost_usd": fees + slip_usd + spread_usd,
        "total_cost_bps": ((fees + slip_usd + spread_usd) / notional * 10_000.0) if notional else None,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "min_abs_slippage": min_abs_slippage,
        "fallback_fill_count": len(fallbacks),
        "fallback_rate": (len(fallbacks) / len(fills)) if fills else 0.0,
    }


def diagnostics(
    signals: list[SignalRecord],
    fills: list[FillRecord],
    trades: list[dict[str, Any]],
    rejects: int,
    equity_curve: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_ts = sorted(s.ts_ns for s in signals)
    gaps = [(b - a) / 60_000_000_000 for a, b in zip(signal_ts, signal_ts[1:])]
    holding = [t["holding_minutes"] for t in trades]
    # time in market from trade intervals clipped to the run window
    intervals = []
    for t in trades:
        if t.get("entry_ts_ns") is not None and t.get("exit_ts_ns") is not None:
            intervals.append((t["entry_ts_ns"], t["exit_ts_ns"]))
    in_market_minutes = 0.0
    for a, b in _merge_intervals(intervals):
        in_market_minutes += (b - a) / 60_000_000_000
    span_minutes = 0.0
    if equity_curve:
        span_minutes = max((int(equity_curve[-1].get("ts_ns") or 0) - int(equity_curve[0].get("ts_ns") or 0)) / 60_000_000_000.0, 0.0)
    avg_gap_minutes = (sum(gaps) / len(gaps)) if gaps else None
    avg_hold_minutes = (sum(holding) / len(holding)) if holding else None
    return {
        "signals_generated": len(signals),
        "orders_submitted": len(fills) + rejects,
        "fills": len(fills),
        "rejects": rejects,
        "trades_completed": len(trades),
        "held_to_settlement": len([t for t in trades if t["exit_reason"] == "settlement"]),
        "early_exits": len([t for t in trades if t["exit_reason"] != "settlement"]),
        "avg_minutes_between_signals": avg_gap_minutes,
        "avg_time_between_signals_s": avg_gap_minutes * 60.0 if avg_gap_minutes is not None else None,
        "avg_holding_minutes": avg_hold_minutes,
        "avg_holding_time_s": avg_hold_minutes * 60.0 if avg_hold_minutes is not None else None,
        "time_in_market_minutes": in_market_minutes,
        "time_in_market_pct": (in_market_minutes / span_minutes * 100.0) if span_minutes > 0 else None,
        "avg_entry_price": (sum(t["entry_price"] for t in trades) / len(trades)) if trades else None,
        "avg_exit_price": (sum(t["exit_price"] for t in trades) / len(trades)) if trades else None,
        "equity_points": len(equity_curve),
    }


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [intervals[0]]
    for a, b in intervals[1:]:
        la, lb = out[-1]
        if a <= lb:
            out[-1] = (la, max(lb, b))
        else:
            out.append((a, b))
    return out


def analyse(
    fills: list[FillRecord],
    signals: list[SignalRecord],
    settlements: dict[str, SettlementRecord],
    bars_by_instrument: dict[str, list[dict[str, Any]]],
    primary_instrument_id: str,
    starting_cash_usd: float,
    evaluation_days: int,
    fee_bps: float,
    slippage_bps: float,
    min_abs_slippage: float,
    rejects: int = 0,
    condition_by_instrument: dict[str, str] | None = None,
) -> dict[str, Any]:
    if condition_by_instrument:
        for f in fills:
            f.condition_id = condition_by_instrument.get(f.instrument_id, f.condition_id)
    trades, positions = reconstruct_trades(fills, settlements, primary_instrument_id)
    curve = build_equity(
        fills,
        bars_by_instrument,
        starting_cash_usd,
        settlements,
        primary_instrument_id,
        condition_by_instrument,
    )
    diag = diagnostics(signals, fills, trades, rejects, curve)
    costs = cost_breakdown(fills, fee_bps, slippage_bps, min_abs_slippage)
    metrics = compute_metrics(
        trades,
        curve,
        starting_cash_usd,
        evaluation_days,
        diag["time_in_market_minutes"],
        len(fills),
        len(signals),
        rejects,
        costs["slippage_usd"],
    )
    return {
        "trades": trades,
        "positions": positions,
        "equity": curve,
        "signals": [s.__dict__ for s in signals],
        "costs": costs,
        "metrics": metrics,
        "diagnostics": diag,
        "phase_breakdown": phase_breakdown(trades),
    }
