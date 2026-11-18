"""End-to-end engine tests on a real Nautilus backtest engine.

No network, no fixture catalog, no ``var/``: two synthetic instruments, six
one-minute bars and a synthetic book.  These tests pin the behaviours that
unit tests can only approximate: bar/quote replay order, the fill model being
called by the matching engine, quantity precision, and fills actually booking
against the strategy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    Currency,
    Money,
    OmsType,
    QuoteTick,
    Quantity,
    TraderId,
    Venue,
)

from backend.engine.execution import (
    BookSnapshot,
    ProfiledFeeModel,
    ProfiledFillModel,
    resolve_profile,
)
from backend.engine.rule_strategy import RuleStrategy
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    ExecutionConfig,
    RiskConfig,
    RunConfig,
    SizingConfig,
    StrategySection,
)

from .conftest import COMPLEMENT_ID, PRIMARY_ID, SLUG

USD = Currency.from_str("USD")
BASE_TS = 1_700_000_000_000_000_000
MINUTE = 60 * 10**9
BAR_TYPE = BarType.from_str(f"{PRIMARY_ID}-1-MINUTE-LAST-EXTERNAL")
COMPLEMENT_BAR_TYPE = BarType.from_str(f"{COMPLEMENT_ID}-1-MINUTE-LAST-EXTERNAL")


def make_bars(instrument, closes):
    bars = []
    for index, close in enumerate(closes):
        ts = BASE_TS + index * MINUTE
        price = instrument.make_price(close)
        high = instrument.make_price(min(close + 0.02, 0.999))
        low = instrument.make_price(max(close - 0.02, 0.001))
        bars.append(
            Bar(
                BAR_TYPE if str(instrument.id) == PRIMARY_ID else COMPLEMENT_BAR_TYPE,
                instrument.make_price(close),
                high,
                low,
                price,
                Quantity.from_str("100.00"),
                ts,
                ts + MINUTE,
            )
        )
    return bars


def make_quotes(instrument, count, *, bid=0.499, ask=0.501):
    quotes = []
    for index in range(count):
        ts = BASE_TS + index * MINUTE
        quotes.append(
            QuoteTick(
                instrument.id,
                instrument.make_price(bid),
                instrument.make_price(ask),
                Quantity.from_str("500.00"),
                Quantity.from_str("500.00"),
                ts,
                ts,
            )
        )
    return quotes


def make_run_config(
    *,
    long_entry=None,
    long_exit=None,
    short_entry=None,
    short_exit=None,
    direction="both",
    settlement="rule_exit",
    execution=None,
    stake=100.0,
) -> RunConfig:
    return RunConfig(
        strategy=StrategySection(
            instrument_id=PRIMARY_ID,
            bar_type=str(BAR_TYPE),
            long_entry=long_entry,
            long_exit=long_exit,
            short_entry=short_entry,
            short_exit=short_exit,
            direction=direction,
            settlement=settlement,
        ),
        execution=execution or ExecutionConfig(profile="conservative"),
        risk=RiskConfig(sizing=SizingConfig(mode="fixed_stake", value=stake)),
        evaluation=EvaluationConfig(date_range=DateRange(start="2024-10-01", end="2026-10-01")),
    )


def run_engine(
    run_config,
    primary,
    complement,
    *,
    closes,
    quote_count=None,
    complement_closes=None,
    execution=None,
    snapshot=None,
    fee_bps=0.0,
):
    resolved = execution or resolve_profile(run_config.execution, has_book_coverage=False)
    provider = _Provider(snapshot) if snapshot is not None else None
    fill_model = ProfiledFillModel(resolved, provider, SLUG)
    fee_model = ProfiledFeeModel(fee_bps)

    engine = BacktestEngine(BacktestEngineConfig(trader_id=TraderId("TESTER-001")))
    engine.add_venue(
        venue=Venue("POLYMARKET"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(10_000, USD)],
        base_currency=USD,
        fill_model=fill_model,
        fee_model=fee_model,
    )
    engine.add_instrument(primary)
    engine.add_instrument(complement)

    bars = make_bars(primary, closes)
    quotes = make_quotes(primary, quote_count or len(closes))
    if complement_closes:
        bars += make_bars(complement, complement_closes)
        quotes += make_quotes(complement, quote_count or len(closes))

    # Bars before quotes: at a shared timestamp the open quote must replay
    # *after* the bar that closes then, so an intent queued on bar N submits at
    # the open of bar N+1 (next-bar-open).
    engine.add_data(bars, sort=True)
    engine.add_data(quotes, sort=True)

    strategy = RuleStrategy(run_config, primary, complement)
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy, fill_model, fee_model


class _Provider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_snapshot(self, condition_id, ts_ns):
        return self.snapshot


def test_long_round_trip_fills_at_next_bar_open(primary, complement):
    run_config = make_run_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
    )
    engine, strategy, fill_model, _ = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.50, 0.50, 0.40, 0.40, 0.40],
    )

    # Entry queued on the close of the 0.50 bar, filled at the next open quote:
    # ask 0.501 + 0.001 absolute slippage.
    assert strategy.trades, "expected one completed round trip"
    trade = strategy.trades[0]
    assert trade["token"] == "YES"
    assert trade["direction"] == "long"
    assert trade["entry_price"] == pytest.approx(0.502)
    assert trade["exit_price"] == pytest.approx(0.498)
    assert trade["quantity"] == pytest.approx(199.60)  # 100 / 0.501 floored to 0.01

    fills = engine.generate_order_fills_report()
    assert len(fills) == 2
    prices = sorted(float(px) for px in fills["avg_px"])
    assert prices == pytest.approx([0.498, 0.502])

    assert strategy.diagnostics()["late_intents"] == 0
    assert strategy.diagnostics()["unfilled_remainders"] == 0
    assert fill_model.last_fill_source == "fixed"
    assert strategy.open_positions() == []


def test_short_round_trip_buys_and_sells_the_complement(primary, complement):
    run_config = make_run_config(
        short_entry={"lt": ["close", 0.45]},
        short_exit={"gt": ["close", 0.45]},
        direction="short",
    )
    engine, strategy, _, _ = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.40, 0.50, 0.50],
        complement_closes=[0.60, 0.60, 0.60, 0.60],
    )

    assert len(strategy.trades) == 1
    trade = strategy.trades[0]
    assert trade["token"] == "NO"
    assert trade["direction"] == "short"
    assert trade["instrument_id"] == COMPLEMENT_ID
    assert trade["entry_price"] == pytest.approx(0.502)
    assert trade["exit_price"] == pytest.approx(0.498)

    fills = engine.generate_fills_report()
    assert set(fills["instrument_id"]) == {COMPLEMENT_ID}


def test_fees_are_charged_on_each_fill(primary, complement):
    run_config = make_run_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
    )
    _, strategy, _, fee_model = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.50, 0.50, 0.40, 0.40],
        fee_bps=20,
    )

    assert fee_model.fee_events == 2
    assert fee_model.total_fee_usd > 0
    assert strategy.trades[0]["fees_usd"] == pytest.approx(fee_model.total_fee_usd, abs=1e-6)


def test_frictionless_profile_has_no_cost(primary, complement):
    run_config = make_run_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
        execution=ExecutionConfig(profile="frictionless"),
    )
    _, strategy, fill_model, _ = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.50, 0.50, 0.40, 0.40],
    )
    assert strategy.trades[0]["entry_price"] == pytest.approx(0.501)
    assert strategy.trades[0]["exit_price"] == pytest.approx(0.499)
    assert fill_model.last_fill_source == "frictionless"


def test_book_partial_depth_leaves_the_rest_unfilled(primary, complement):
    snapshot = BookSnapshot(
        condition_id=SLUG,
        ts_ns=BASE_TS + 2 * MINUTE - 1,  # just before the entry quote
        bids=[(0.410, 50.0)],
        asks=[(0.420, 1.0)],
    )
    execution = resolve_profile(ExecutionConfig(profile="book"), has_book_coverage=True)
    run_config = make_run_config(
        long_entry={"gt": ["close", 0.45]},
        direction="long",
        execution=ExecutionConfig(profile="book"),
    )
    engine, strategy, fill_model, _ = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.50, 0.50, 0.50],
        execution=execution,
        snapshot=snapshot,
    )

    assert fill_model.last_fill_source == "book"
    assert strategy.position is not None
    assert strategy.position.quantity == pytest.approx(1.0)
    assert strategy.diagnostics()["unfilled_remainders"] >= 1

    fills = engine.generate_order_fills_report()
    assert len(fills) == 1
    assert float(fills["avg_px"].iloc[0]) == pytest.approx(0.420)
    assert float(fills["filled_qty"].iloc[0]) == pytest.approx(1.0)


def test_lifecycle_and_warmup_never_trade_without_data(primary, complement):
    run_config = make_run_config(
        long_entry={"gt": ["close", 0.99]},
        direction="long",
    )
    engine, strategy, _, _ = run_engine(
        run_config,
        primary,
        complement,
        closes=[0.40, 0.41, 0.42, 0.43],
    )
    assert strategy.trades == []
    assert strategy.open_positions() == []
    assert engine.generate_order_fills_report().empty
