"""Prepare and execute a single backtest.

The API process prepares a run (validation, bars, profile, hashes) and the run
itself executes in a separate worker process.  Nautilus is used for the
instrument model, order matching and indicator objects; analytics are computed
from the resulting fills and stored bars.
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from backend.config import code_version, nautilus_version
from backend.data.versions import run_data_version
from backend.rules.schema import RunConfig, run_hash, split_hash, strategy_hash


def _ns(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v > 10**17 else v * 1_000_000_000
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return None


def _date_ns(value: str, end: bool = False) -> int:
    d = date.fromisoformat(value[:10])
    base = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return base + (86_400_000_000_000 - 1 if end else 0)


def _instrument_row(instrument_id: str) -> dict[str, Any]:
    from backend.data.instruments import get_instrument

    row = get_instrument(instrument_id)
    if row is None:
        raise ValueError(f"instrument {instrument_id} is not indexed")
    return dict(row) if not isinstance(row, dict) else row


def _complement_id(primary_id: str) -> str:
    if primary_id.endswith("-YES.POLYMARKET"):
        return primary_id[: -len("-YES.POLYMARKET")] + "-NO.POLYMARKET"
    if primary_id.endswith("-NO.POLYMARKET"):
        return primary_id[: -len("-NO.POLYMARKET")] + "-YES.POLYMARKET"
    raise ValueError(f"cannot derive complement for {primary_id}")


def _ensure_derived() -> None:
    from backend.config import LIFECYCLE_PATH, SETTLEMENT_PATH
    from backend.data.lifecycle import build_match_lifecycle
    from backend.data.settlement import build_settlement

    if not LIFECYCLE_PATH.exists():
        build_match_lifecycle()
    if not SETTLEMENT_PATH.exists():
        build_settlement()


def prepare_run(config: RunConfig) -> dict[str, Any]:
    """Validate a request against stored data and resolve its execution."""
    from backend.data.books import book_coverage
    from backend.data.bars import build_bars
    from backend.data.readmodel import load_bars
    from backend.engine.execution import resolve_profile

    _ensure_derived()
    primary_id = config.strategy.instrument_id
    primary = _instrument_row(primary_id)
    complement_id = _complement_id(primary_id)
    complement = _instrument_row(complement_id)
    alias = config.strategy.bar_type
    start_ns = _date_ns(config.evaluation.date_range.start)
    end_ns = _date_ns(config.evaluation.date_range.end, end=True)

    for iid in (primary_id, complement_id):
        build_bars(iid, alias)
        page = load_bars(iid, alias, start_ns, end_ns, 1)
        rows = page.get("bars") if isinstance(page, dict) else page
        if not rows:
            raise ValueError(
                f"no {alias} bars for {iid} in {config.evaluation.date_range.start}..{config.evaluation.date_range.end}"
            )

    coverage = book_coverage(primary_id, primary.get("match_key"))
    window = coverage.get("window") or {}
    execution = resolve_profile(
        config.execution,
        has_book_coverage=bool(coverage.get("has_coverage")),
        coverage_window=(window.get("start_ns"), window.get("end_ns"))
        if window.get("start_ns") and window.get("end_ns")
        else None,
    )
    dv = run_data_version()
    cv = code_version()
    sh = strategy_hash(config)
    rh = run_hash(config, dv, cv)
    # Record the advisory quality warnings in the manifest. Warm-up is the
    # largest declared warm-up among the strategy's indicator instances.
    warmup_bars: int | None = None
    try:
        from backend.indicators.compute import build_indicators

        warmups = build_indicators(
            [indicator.model_dump(mode="json") for indicator in config.strategy.indicators]
        ).warmup()
        values = [int(value) for value in warmups.values() if value is not None]
        warmup_bars = max(values) if values else None
    except Exception:  # noqa: BLE001 - advisory only
        warmup_bars = None
    quality_report: dict[str, Any] | None = None
    quality_warnings: list[dict[str, Any]] = []
    try:
        from backend.data.quality import quality as quality_for

        quality_report = quality_for(primary_id, start_ns, end_ns, warmup_bars=warmup_bars)
        quality_warnings = list(quality_report.get("warnings") or [])
    except Exception:  # noqa: BLE001 - advisory only
        quality_report = None
    return {
        "config": config.model_dump(mode="json", exclude_none=True),
        "primary": primary,
        "complement": complement,
        "instrument_id": primary_id,
        "complement_instrument_id": complement_id,
        "bar_alias": alias,
        "data_version": dv,
        "code_version": cv,
        "strategy_hash": sh,
        "run_hash": rh,
        "split_hash": split_hash(config),
        "requested_profile": execution.requested,
        "effective_profile": execution.effective,
        "fallback_reason": execution.fallback_reason,
        "book_coverage": coverage,
        "execution": execution.__dict__,
        "quality": quality_report,
        "warnings": quality_warnings,
    }


# ---------------------------------------------------------------------------
# Execution


@dataclass
class RunOutcome:
    status: str
    result: dict[str, Any] | None
    manifest: dict[str, Any]
    log: str
    error: str | None = None


def _bar_dict(row: dict[str, Any]) -> dict[str, Any]:
    ts_event = int(row.get("ts_event_ns") or _ns(row.get("ts_event")) or 0)
    ts_init = int(row.get("ts_init_ns") or _ns(row.get("ts_init")) or 0) or ts_event + 1
    return {
        "ts_event_ns": ts_event,
        "ts_init_ns": ts_init,
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row.get("volume") or 0.0),
        "trade_count": int(row.get("trade_count") or 0),
    }


def execute_run(config_dict: dict[str, Any], prepared: dict[str, Any] | None = None) -> RunOutcome:
    """Run one backtest; returns analytics and the manifest (no DB writes)."""
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig
    from nautilus_trader.model import AggregationSource, AccountType, Bar, BarType, BookType, Currency, Money, OmsType, QuoteTick, Venue

    from backend.data.lifecycle import lifecycle_for, phase_at
    from backend.data.readmodel import load_bars
    from backend.data.settlement import get_settlement
    from backend.engine.analytics import FillRecord, SettlementRecord, SignalRecord, analyse
    from backend.engine.execution import ProfiledFeeModel, ProfiledFillModel, resolve_profile
    from backend.engine.instruments import build_binary_option
    from backend.engine.rule_strategy import RuleStrategy, RuleStrategyConfig, bar_to_dict
    from backend.engine.signals import signal_plan_for_config

    started = time.time()
    config = RunConfig.model_validate(config_dict)
    prepared = prepared or prepare_run(config)
    primary_row = dict(prepared["primary"])
    complement_row = dict(prepared["complement"])
    primary_row.setdefault("outcome", "YES")
    complement_row.setdefault("outcome", "NO")
    primary_row.setdefault("raw_symbol", primary_row.get("token_symbol") or primary_row.get("instrument_id"))
    complement_row.setdefault("raw_symbol", complement_row.get("token_symbol") or complement_row.get("instrument_id"))
    if str(primary_row.get("instrument_id", "")).endswith("-NO.POLYMARKET"):
        primary_row["outcome"] = "NO"
    if str(complement_row.get("instrument_id", "")).endswith("-YES.POLYMARKET"):
        complement_row["outcome"] = "YES"
    primary = build_binary_option(primary_row)
    complement = build_binary_option(complement_row)
    alias = config.strategy.bar_type
    start_ns = _date_ns(config.evaluation.date_range.start)
    end_ns = _date_ns(config.evaluation.date_range.end, end=True)

    def load(iid: str) -> list[dict[str, Any]]:
        got = load_bars(iid, alias, start_ns, end_ns, 200_000)
        rows = got.get("bars") if isinstance(got, dict) else got
        return [_bar_dict(r) for r in rows]

    bars_primary = load(primary.id.value)
    bars_complement = load(complement.id.value)
    if not bars_primary:
        raise ValueError("no bars for primary instrument")
    coverage = prepared.get("book_coverage") or {}
    window = coverage.get("window") or {}
    execution = resolve_profile(
        config.execution,
        has_book_coverage=bool(coverage.get("has_coverage")),
        coverage_window=(window.get("start_ns"), window.get("end_ns"))
        if window.get("start_ns") and window.get("end_ns")
        else None,
    )

    from backend.data.books import CompactBookProvider

    book_provider = None
    if execution.book_walk and coverage.get("has_coverage"):
        try:
            book_provider = CompactBookProvider(primary.id.value)
        except Exception:  # noqa: BLE001
            book_provider = None
    fill_model = ProfiledFillModel(
        execution,
        snapshot_provider=book_provider,
        condition_id=primary_row["condition_id"],
    )
    fee_model = ProfiledFeeModel(execution.fee_bps)

    def replay_bar_type(instrument) -> Any:
        # Precomputed bars are supplied to the engine as EXTERNAL. The data
        # layer's canonical composite type stays on the artifact and the chart;
        # only the replay type differs so Nautilus will dispatch it.
        if alias == "1m":
            return BarType.from_str(f"{instrument.id.value}-1-MINUTE-LAST-EXTERNAL")
        from backend.data.bars import parse_bar_alias

        parsed = BarType.from_str(parse_bar_alias(alias, instrument.id.value))
        if parsed.aggregation_source == AggregationSource.EXTERNAL:
            return parsed
        return BarType(instrument.id, parsed.spec, AggregationSource.EXTERNAL)

    def build_bars(rows: list[dict[str, Any]], instrument) -> tuple[list[Bar], list[QuoteTick]]:
        bt = replay_bar_type(instrument)
        bars_out: list[Bar] = []
        quotes: list[QuoteTick] = []
        for r in rows:
            price = instrument.make_price(r["close"])
            qty = instrument.make_qty(max(r["volume"], 0.01))
            bars_out.append(
                Bar(
                    bar_type=bt,
                    open=instrument.make_price(r["open"]),
                    high=instrument.make_price(r["high"]),
                    low=instrument.make_price(r["low"]),
                    close=price,
                    volume=qty,
                    ts_event=r["ts_event_ns"],
                    ts_init=r.get("ts_init_ns") or (r["ts_event_ns"] + 60_000_000_000),
                )
            )
            quote_size = instrument.make_qty(1)
            quotes.append(
                QuoteTick(
                    instrument_id=instrument.id,
                    bid_price=instrument.make_price(r["open"]),
                    ask_price=instrument.make_price(r["open"]),
                    bid_size=quote_size,
                    ask_size=quote_size,
                    ts_event=r["ts_event_ns"],
                    ts_init=r["ts_event_ns"] + 1,
                )
            )
        return bars_out, quotes

    bars_p, quotes_p = build_bars(bars_primary, primary)
    bars_c, quotes_c = build_bars(bars_complement, complement)

    lc_row = lifecycle_for(primary_row.get("match_key"))

    settlement_row = get_settlement(primary_row["condition_id"])
    # The brief defines expiry as the market's end_date. Prefer it for the
    # settlement timestamp; markets.updated_at is a metadata timestamp, not a
    # verified resolution time, and would inflate holding periods.
    settled_ts = primary_row.get("end_date_utc") or primary_row.get("end_date")
    settlement_ts_source = "market_end_date"
    if settled_ts is None and settlement_row is not None:
        settled_ts = getattr(settlement_row, "settled_ts_utc", None)
        settlement_ts_source = "markets_updated_at"

    def lifecycle_at(ts_ns: int) -> dict[str, Any]:
        from backend.data.lifecycle import time_since_kickoff_minutes, time_to_kickoff_minutes, time_to_settlement_minutes
        from backend.data.timeutil import from_ns

        if lc_row is None:
            return {
                "phase": "unknown",
                "time_to_kickoff_minutes": None,
                "time_since_kickoff_minutes": None,
                "time_to_settlement_minutes": None,
                "market_status": None,
            }
        when = from_ns(ts_ns)
        return {
            "phase": phase_at(lc_row, when, settled_ts),
            "time_to_kickoff_minutes": time_to_kickoff_minutes(lc_row, when),
            "time_since_kickoff_minutes": time_since_kickoff_minutes(lc_row, when),
            "time_to_settlement_minutes": time_to_settlement_minutes(lc_row, when, settled_ts),
            "market_status": None,
        }

    full_bar_type = str(replay_bar_type(primary))
    lifecycle_version = _lifecycle_version()
    # Signals are execution-independent: the plan is computed from exactly the
    # bars Nautilus will dispatch (bar_to_dict round-trips the instrument's
    # price precision), so plan and runtime truths are identical.  The default
    # cache is two-tier, so a worker reads a plan persisted by an earlier one.
    signal_cache_hit = signal_plan_for_config(
        config,
        [bar_to_dict(bar) for bar in bars_p],
        lifecycle_at,
        data_version=prepared["data_version"],
        lifecycle_version=lifecycle_version,
    )
    signal_plan = signal_cache_hit.plan
    signals: list[dict[str, Any]] = []
    fill_sink: list[dict[str, Any]] = []
    strategy = RuleStrategy(
        RuleStrategyConfig(
            run_config=config,
            primary=primary,
            complement=complement,
            signal_sink=signals,
            fill_sink=fill_sink,
            fill_metadata=lambda: {
                "execution_source": fill_model.last_fill_source,
                "fallback_reason": fill_model.last_fallback_reason,
                "impact_bps": fill_model.last_impact_bps,
            },
            lifecycle_lookup=lifecycle_at,
            signal_plan=signal_plan,
        )
    )

    eng = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    eng.add_venue(
        venue=Venue("POLYMARKET"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(Decimal(str(config.risk.sizing.bankroll_usd)), Currency.from_str("USD"))],
        base_currency=Currency.from_str("USD"),
        book_type=BookType.L1_MBP,
        # Only externally aggregated 1m bars drive Nautilus matching. Composite
        # and non-time bars are delivered to the strategy; their fills come
        # from the open quote on the following bar.
        bar_execution=(alias == "1m"),
        fill_model=fill_model,
        fee_model=fee_model,
        use_random_ids=False,
        use_position_ids=True,
    )
    try:
        strategy.strategy_config.bar_type = full_bar_type
    except Exception:  # noqa: BLE001
        pass
    eng.add_instrument(primary)
    eng.add_instrument(complement)
    # Bars first, then the open quotes: at a shared timestamp the signal bar
    # closes before the next open quote is replayed, which is the configured
    # next-bar-open fill convention.
    eng.add_data(bars_p, validate=False)
    eng.add_data(bars_c, validate=False)
    eng.add_data(quotes_p)
    eng.add_data(quotes_c)
    eng.add_strategy(strategy)
    eng.run()
    if book_provider is not None:
        book_provider.close()

    fill_source = (
        "book"
        if execution.effective == "book"
        else ("conservative_fallback" if execution.requested == "book" else "fixed")
    )
    fills: list[FillRecord] = []
    for raw in fill_sink:
        ts_ns = int(raw["ts_ns"])
        lc = lifecycle_at(ts_ns)
        fills.append(
            FillRecord(
                ts_ns=ts_ns,
                instrument_id=str(raw["instrument_id"]),
                condition_id=str(primary_row["condition_id"]),
                token=str(raw["token"]),
                side=str(raw["side"]),
                quantity=float(raw["quantity"]),
                price=float(raw["price"]),
                fee_usd=float(raw["fee_usd"]),
                reference_price=raw.get("reference_price"),
                execution_source=str(raw.get("execution_source") or fill_source),
                fallback_reason=raw.get("fallback_reason"),
                impact_bps=raw.get("impact_bps"),
                phase=lc.get("phase", "unknown"),
            )
        )
    diag = strategy.diagnostics()
    rejects = int(diag.get("rejects", 0))
    signal_records = []
    for raw in signals:
        action = str(raw.get("action", ""))
        ts_ns = int(raw.get("ts_ns", 0))
        lc = lifecycle_at(ts_ns)
        signal_records.append(
            SignalRecord(
                ts_ns=ts_ns,
                action=action,
                instrument_id=primary.id.value if action.startswith("long") else complement.id.value,
                token=primary_row.get("token") or "YES" if action.startswith("long") else complement_row.get("token") or "NO",
                reference_price=raw.get("ref_price"),
                phase=lc.get("phase", "unknown"),
            )
        )
    settlements: dict[str, SettlementRecord] = {}
    if settlement_row is not None:
        settled_ns = _ns(settled_ts)
        settlements[primary_row["condition_id"]] = SettlementRecord(
            condition_id=primary_row["condition_id"],
            yes_settlement=float(
                getattr(settlement_row, "yes_settlement", None)
                if not isinstance(settlement_row, dict)
                else settlement_row.get("yes_settlement")
            ),
            no_settlement=float(
                getattr(settlement_row, "no_settlement", None)
                if not isinstance(settlement_row, dict)
                else settlement_row.get("no_settlement")
            ),
            settled_ts_ns=settled_ns,
            source=settlement_ts_source,
        )

    evaluation_days = (date.fromisoformat(config.evaluation.date_range.end) - date.fromisoformat(config.evaluation.date_range.start)).days
    result = analyse(
        fills,
        signal_records,
        settlements,
        {primary.id.value: bars_primary, complement.id.value: bars_complement},
        primary.id.value,
        config.risk.sizing.bankroll_usd,
        max(evaluation_days, 1),
        execution.fee_bps,
        execution.slippage_bps,
        execution.min_abs_slippage,
        rejects=rejects,
        condition_by_instrument={primary.id.value: primary_row["condition_id"], complement.id.value: primary_row["condition_id"]},
    )

    # In-sample / out-of-sample slices, when a split exists.
    split = config.evaluation.split
    if split is not None:
        test_ns = _date_ns(split.test_start)
        oos_trades = [t for t in result["trades"] if (t.get("entry_ts_ns") or 0) >= test_ns]
        is_trades = [t for t in result["trades"] if (t.get("entry_ts_ns") or 0) < test_ns]
        result["in_sample"] = _subset_metrics(is_trades, config, execution)
        result["out_of_sample"] = _subset_metrics(oos_trades, config, execution)
        result["oos_trades"] = oos_trades

    manifest = {
        "run_id": None,
        "strategy_hash": prepared["strategy_hash"],
        "run_hash": prepared["run_hash"],
        "data_version": prepared["data_version"],
        "code_version": prepared["code_version"],
        "nautilus_version": nautilus_version(),
        "lifecycle_version": lifecycle_version,
        "signal_cache": {
            **signal_plan.metadata(),
            "hit": signal_cache_hit.hit,
            "source": signal_cache_hit.source,
        },
        "source_paths": {
            "tape": "polymarket/{football,epl}/*_moneyline_tape.parquet",
            "markets": "polymarket/sii/markets.parquet",
            "settlement": "results/polymarket_markets.parquet",
            "lifecycle": "var/lifecycle/match_lifecycle.parquet",
        },
        "instrument_id": primary.id.value,
        "complement_instrument_id": complement.id.value,
        "condition_id": primary_row["condition_id"],
        "bar_type": alias,
        "aggregation_chain": "1-MINUTE-LAST-EXTERNAL" if alias == "1m" else f"{alias} from 1-MINUTE-LAST-EXTERNAL",
        "execution": {
            "requested": execution.requested,
            "effective": execution.effective,
            "cost_source": execution.cost_source,
            "fee_bps": execution.fee_bps,
            "slippage_bps": execution.slippage_bps,
            "min_abs_slippage": execution.min_abs_slippage,
            "fallback_reason": execution.fallback_reason,
            "fallback_rate": result["costs"].get("fallback_rate"),
            "book_coverage": coverage,
            "book_staleness_s": config.execution.book_staleness_s,
            "max_depth_levels": config.execution.max_depth_levels,
        },
        "evaluation": {
            "date_range": config.evaluation.date_range.model_dump(mode="json"),
            "split": split.model_dump(mode="json") if split else None,
            "split_hash": prepared.get("split_hash"),
            "holdout_reveal_count": 0,
        },
        "risk": config.risk.model_dump(mode="json"),
        "acknowledged_warnings": list(config.acknowledged_warnings),
        "lifecycle_source": "espn_keyevents",
        "quality": prepared.get("quality"),
        "warnings": list(prepared.get("warnings") or [])
        + (
            [
                {
                    "code": "zero_signals",
                    "message": "The strategy produced no entry signals in this range.",
                }
            ]
            if not signals
            else []
        ),
        "elapsed_s": round(time.time() - started, 3),
    }
    log = "\n".join(
        [
            f"run config: {json.dumps(config.canonical(), sort_keys=True)}",
            f"bars: primary={len(bars_primary)} complement={len(bars_complement)} bar_type={full_bar_type}",
            f"signals: cache_hit={signal_cache_hit.hit} source={signal_cache_hit.source} "
            f"rows={len(signal_plan)} spec={signal_plan.spec_hash} plan_hash={signal_plan.plan_hash}",
            f"strategy: {json.dumps(strategy.diagnostics(), default=str)}",
            f"fills: {len(fills)} rejects: {rejects}",
            f"trades: {len(result['trades'])}",
            f"elapsed_s: {manifest['elapsed_s']}",
        ]
    )
    return RunOutcome("complete", result, manifest, log)


def _lifecycle_version() -> str | None:
    try:
        import pyarrow.parquet as pq
        from backend.config import LIFECYCLE_PATH

        if LIFECYCLE_PATH.exists():
            md = pq.read_metadata(LIFECYCLE_PATH).metadata or {}
            raw = md.get(b"lifecycle_version")
            return raw.decode() if raw else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _subset_metrics(trades: list[dict[str, Any]], config: RunConfig, execution) -> dict[str, Any]:
    from backend.engine.analytics import compute_metrics

    start = config.risk.sizing.bankroll_usd
    net = sum(t["net_pnl_usd"] for t in trades)
    return {
        "trade_count": len(trades),
        "net_pnl_usd": net,
        "return_pct": (net / start * 100.0) if start else None,
        "win_rate": (len([t for t in trades if t["net_pnl_usd"] > 0]) / len(trades)) if trades else None,
        "avg_yes_points_captured": (sum(t["exit_yes_prob"] - t["entry_yes_prob"] for t in trades) / len(trades)) if trades else None,
        "fees_usd": sum(t["fees_usd"] for t in trades),
    }
