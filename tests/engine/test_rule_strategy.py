from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine.rule_strategy import (
    OperandResolver,
    OrderIntent,
    RuleStrategy,
    collect_cross_operands,
    compute_sizing,
    evaluate_condition,
    position_sizing,
    stake_for,
)
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    IndicatorInstance,
    LifecycleFilter,
    RiskConfig,
    RunConfig,
    SizingConfig,
    StrategySection,
)

from .conftest import COMPLEMENT_ID, PRIMARY_ID, make_bar, make_quote

TS0 = 1_700_000_000_000_000_000
BAR = 60 * 10**9
FULL_BAR_TYPE = f"{PRIMARY_ID}-1-MINUTE-LAST-EXTERNAL"


# ---------------------------------------------------------------------------
# Fakes: the indicator set is the sibling package's unit, so tests inject a
# deterministic series; the strategy under test is the real one.


class FakeIndicatorSet:
    def __init__(self, series: dict[str, list[float | None]]) -> None:
        self.series = series
        self.index = 0
        self.current: dict[str, dict[str, float | None]] = {}

    def update(self, _bar):
        self.current = {
            instance_id: {"value": values[self.index]}
            for instance_id, values in self.series.items()
        }
        self.index += 1
        return self

    def values(self):
        return self.current


def fake_factory(series: dict[str, list[float | None]]):
    def factory(_instances):
        return FakeIndicatorSet(series)

    return factory


class RecordingStrategy(RuleStrategy):
    """The real strategy; only the venue submission is recorded."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted: list[dict] = []

    def _submit_market(self, instrument, side, quantity, ts_ns):
        self.submitted.append(
            {
                "instrument_id": str(instrument.id),
                "side": side,
                "quantity": float(quantity),
                "ts_ns": ts_ns,
            }
        )
        self._diagnostics["orders_submitted"] += 1


def make_config(
    *,
    long_entry=None,
    long_exit=None,
    short_entry=None,
    short_exit=None,
    indicators=None,
    direction="both",
    settlement="rule_exit",
    sizing=None,
    lifecycle_filters=None,
) -> RunConfig:
    section = StrategySection(
        instrument_id=PRIMARY_ID,
        bar_type=FULL_BAR_TYPE,
        indicators=indicators or [],
        long_entry=long_entry,
        long_exit=long_exit,
        short_entry=short_entry,
        short_exit=short_exit,
        direction=direction,
        settlement=settlement,
        lifecycle_filters=lifecycle_filters,
    )
    return RunConfig(
        strategy=section,
        risk=RiskConfig(sizing=sizing or SizingConfig()),
        evaluation=EvaluationConfig(date_range=DateRange(start="2024-10-01", end="2026-10-01")),
    )


def ma_indicator(instance_id: str = "ma", period: int = 5) -> IndicatorInstance:
    return IndicatorInstance(instance_id=instance_id, type="sma", params={"period": period})


def make_strategy(config, primary, complement, *, series=None, lifecycle=None):
    factory = fake_factory(series) if series is not None else None
    return RecordingStrategy(
        config,
        primary=primary,
        complement=complement,
        indicator_specs=config.strategy.indicators,
        lifecycle_lookup=lifecycle,
        indicator_set_factory=factory,
    )


def fill_event(*, instrument_id, is_buy, quantity, price, ts, fee=0.0):
    from nautilus_trader.model import Currency, Money, Price, Quantity

    return SimpleNamespace(
        instrument_id=instrument_id,
        is_buy=is_buy,
        last_qty=Quantity.from_str(quantity),
        last_px=Price.from_str(price),
        commission=Money(float(fee), Currency.from_str("USD")),
        ts_event=int(ts),
        client_order_id="O-TEST-1",
    )


# ---------------------------------------------------------------------------
# Evaluator


def test_evaluate_condition_comparators_and_nulls():
    values = {"a": 2.0, "b": 1.0, "missing": None}
    resolver = OperandResolver(bar={}, indicators={}, lifecycle={})
    resolve = lambda operand: values.get(operand) if isinstance(operand, str) else operand  # noqa: E731

    assert evaluate_condition({"gt": ["a", "b"]}, resolve)
    assert evaluate_condition({"gte": ["a", "a"]}, resolve)
    assert evaluate_condition({"lt": ["b", "a"]}, resolve)
    assert evaluate_condition({"lte": ["a", "a"]}, resolve)
    assert not evaluate_condition({"gt": ["missing", "b"]}, resolve)
    assert not evaluate_condition({"gt": ["a", "missing"]}, resolve)
    assert evaluate_condition({"all": [{"gt": ["a", "b"]}, {"lt": ["b", "a"]}]}, resolve)
    assert evaluate_condition({"any": [{"gt": ["b", "a"]}, {"gt": ["a", "b"]}]}, resolve)
    assert evaluate_condition({"not": {"gt": ["b", "a"]}}, resolve)
    assert resolver.indicators == {}


def test_evaluate_crosses_uses_the_previous_bar():
    resolver = OperandResolver(bar={"close": 0.6}, indicators={"ma": {"value": 0.5}})
    tree = {"crosses_above": ["close", "indicator:ma:value"]}

    assert not evaluate_condition(tree, resolver, previous=None)
    assert not evaluate_condition(
        tree, resolver, previous={"close": None, "indicator:ma:value": 0.5}
    )
    assert evaluate_condition(
        tree, resolver, previous={"close": 0.4, "indicator:ma:value": 0.5}
    )
    assert not evaluate_condition(
        tree, resolver, previous={"close": 0.6, "indicator:ma:value": 0.5}
    )

    down = OperandResolver(bar={"close": 0.4}, indicators={"ma": {"value": 0.5}})
    assert evaluate_condition(
        {"crosses_below": ["close", "indicator:ma:value"]},
        down,
        previous={"close": 0.6, "indicator:ma:value": 0.5},
    )


def test_collect_cross_operands():
    out: set[str] = set()
    collect_cross_operands(
        {
            "all": [
                {"crosses_above": ["close", "indicator:ma:value"]},
                {"gt": ["indicator:rsi:value", 50]},
                {"not": {"crosses_below": ["open", "indicator:ma:value"]}},
            ]
        },
        out,
    )
    assert out == {"close", "open", "indicator:ma:value"}


def test_operand_resolver_rejects_unknown_operand():
    with pytest.raises(ValueError):
        OperandResolver().value("indicator:oops")


# ---------------------------------------------------------------------------
# Sizing


def test_sizing_math_fixed_stake(primary):
    sizing = SizingConfig(mode="fixed_stake", value=100.0)
    assert stake_for(sizing, 0.42) == 100.0
    quantity = compute_sizing(sizing, 0.42, primary)
    assert float(quantity) == pytest.approx(238.09)  # 100 / 0.42 floored to 0.01

    summary = position_sizing(sizing, 0.42, primary)
    assert summary["contracts"] == pytest.approx(238.09)
    assert summary["notional_usd"] == pytest.approx(238.09 * 0.42, abs=1e-5)
    assert summary["max_loss_usd"] == pytest.approx(238.09 * 0.42, abs=1e-5)
    assert summary["max_payout_usd"] == pytest.approx(238.09)
    assert summary["max_profit_usd"] == pytest.approx(238.09 - 238.09 * 0.42, abs=1e-5)


def test_sizing_math_risk_pct(primary):
    sizing = SizingConfig(mode="risk_pct", value=2.0, bankroll_usd=1000.0)
    assert stake_for(sizing, 0.42) == pytest.approx(20.0)
    assert float(compute_sizing(sizing, 0.42, primary)) == pytest.approx(47.61)


def test_sizing_floors_to_one_increment(primary):
    sizing = SizingConfig(mode="fixed_stake", value=0.0001)
    quantity = compute_sizing(sizing, 0.99, primary)
    assert float(quantity) == pytest.approx(0.01)


def test_sizing_rejects_bad_reference(primary):
    with pytest.raises(ValueError):
        stake_for(SizingConfig(), 1.2)


# ---------------------------------------------------------------------------
# RuleStrategy: intents


def test_long_entry_and_exit_trade_the_primary_token(primary, complement):
    config = make_config(
        indicators=[ma_indicator()],
        long_entry={"gt": ["close", "indicator:ma:value"]},
        long_exit={"lt": ["close", "indicator:ma:value"]},
        direction="long",
        sizing=SizingConfig(mode="fixed_stake", value=100.0),
    )
    strategy = make_strategy(
        config, primary, complement, series={"ma": [0.55, 0.55, 0.55, 0.55]}
    )

    strategy.on_bar(make_bar(TS0, 0.50))
    strategy.on_quote(make_quote(TS0 + BAR, bid=0.499, ask=0.501))
    assert strategy.submitted == []
    assert strategy.signals == []

    strategy.on_bar(make_bar(TS0 + BAR, 0.60))
    assert strategy.pending_intent is not None
    assert strategy.pending_intent.action == "long_entry"
    assert strategy.pending_intent.side == "buy"
    assert strategy.pending_intent.instrument_id == PRIMARY_ID
    assert strategy.pending_intent.token == "YES"

    strategy.on_quote(make_quote(TS0 + 2 * BAR, bid=0.499, ask=0.501))
    assert len(strategy.submitted) == 1
    assert strategy.submitted[0]["instrument_id"] == PRIMARY_ID
    assert strategy.submitted[0]["side"] == "buy"
    assert strategy.submitted[0]["quantity"] == pytest.approx(199.60)  # 100 / 0.501

    strategy.on_order_filled(
        fill_event(
            instrument_id=PRIMARY_ID,
            is_buy=True,
            quantity="199.60",
            price="0.502",
            ts=TS0 + 2 * BAR,
        )
    )
    assert strategy.position is not None
    assert strategy.position.token == "YES"
    assert strategy.position.direction == "long"

    # A bar that still satisfies the entry rule must not pyramid.
    strategy.on_bar(make_bar(TS0 + 2 * BAR, 0.62))
    strategy.on_quote(make_quote(TS0 + 3 * BAR, bid=0.499, ask=0.501))
    assert len(strategy.submitted) == 1

    strategy.on_bar(make_bar(TS0 + 3 * BAR, 0.40))
    assert strategy.pending_intent is not None
    assert strategy.pending_intent.action == "long_exit"
    assert strategy.pending_intent.side == "sell"
    strategy.on_quote(make_quote(TS0 + 4 * BAR, bid=0.399, ask=0.401))
    assert strategy.submitted[1]["side"] == "sell"
    assert strategy.submitted[1]["instrument_id"] == PRIMARY_ID

    strategy.on_order_filled(
        fill_event(
            instrument_id=PRIMARY_ID,
            is_buy=False,
            quantity="199.60",
            price="0.398",
            ts=TS0 + 4 * BAR,
        )
    )
    assert strategy.position is None
    assert len(strategy.trades) == 1
    trade = strategy.trades[0]
    assert trade["token"] == "YES"
    assert trade["direction"] == "long"
    assert trade["entry_price"] == pytest.approx(0.502)
    assert trade["exit_price"] == pytest.approx(0.398)
    assert trade["pnl_usd"] == pytest.approx((0.398 - 0.502) * 199.60, abs=1e-6)
    assert strategy.diagnostics()["trades_completed"] == 1


def test_short_entry_buys_the_complement_token(primary, complement):
    config = make_config(
        indicators=[ma_indicator()],
        short_entry={"lt": ["close", "indicator:ma:value"]},
        short_exit={"gt": ["close", "indicator:ma:value"]},
        direction="short",
    )
    strategy = make_strategy(
        config, primary, complement, series={"ma": [0.55, 0.55, 0.55]}
    )

    strategy.on_bar(make_bar(TS0, 0.40))
    intent = strategy.pending_intent
    assert intent is not None
    assert intent.action == "short_entry"
    assert intent.side == "buy"
    assert intent.token == "NO"
    assert intent.instrument_id == COMPLEMENT_ID

    strategy.on_quote(make_quote(TS0 + BAR, bid=0.599, ask=0.601, instrument_id=COMPLEMENT_ID))
    assert strategy.submitted[0]["instrument_id"] == COMPLEMENT_ID

    strategy.on_order_filled(
        fill_event(
            instrument_id=COMPLEMENT_ID,
            is_buy=True,
            quantity="10.00",
            price="0.602",
            ts=TS0 + BAR,
        )
    )
    assert strategy.position is not None
    assert strategy.position.token == "NO"
    assert strategy.position.direction == "short"

    strategy.on_bar(make_bar(TS0 + BAR, 0.60))
    assert strategy.pending_intent is not None
    assert strategy.pending_intent.action == "short_exit"
    assert strategy.pending_intent.side == "sell"
    assert strategy.pending_intent.instrument_id == COMPLEMENT_ID


def test_intent_sides_and_tokens_for_all_actions(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )

    assert strategy._intent_for("long_entry") == OrderIntent(
        "long_entry", PRIMARY_ID, "YES", "buy"
    )
    assert strategy._intent_for("long_exit") == OrderIntent(
        "long_exit", PRIMARY_ID, "YES", "sell"
    )
    assert strategy._intent_for("short_entry") == OrderIntent(
        "short_entry", COMPLEMENT_ID, "NO", "buy"
    )
    assert strategy._intent_for("short_exit") == OrderIntent(
        "short_exit", COMPLEMENT_ID, "NO", "sell"
    )


def test_no_trades_during_indicator_warmup(primary, complement):
    config = make_config(
        indicators=[ma_indicator()],
        long_entry={"gt": ["close", "indicator:ma:value"]},
        direction="long",
    )
    strategy = make_strategy(
        config, primary, complement, series={"ma": [None, None, 0.50, 0.50]}
    )

    for index, close in enumerate([0.60, 0.61, 0.62, 0.63]):
        strategy.on_bar(make_bar(TS0 + index * BAR, close))
        if index < 2:
            strategy.on_quote(make_quote(TS0 + (index + 1) * BAR, bid=0.599, ask=0.601))

    assert strategy.submitted == []
    assert strategy.diagnostics()["indicator_not_ready_bars"] == 2
    assert [signal["ts_ns"] for signal in strategy.signals] == [
        TS0 + 2 * BAR,
        TS0 + 3 * BAR,
    ]


def test_crosses_need_two_bars(primary, complement):
    config = make_config(
        indicators=[ma_indicator()],
        long_entry={"crosses_above": ["close", "indicator:ma:value"]},
        direction="long",
    )
    strategy = make_strategy(
        config, primary, complement, series={"ma": [0.50, 0.50, 0.50]}
    )

    strategy.on_bar(make_bar(TS0, 0.40))
    assert strategy.pending_intent is None  # no previous bar
    strategy.on_bar(make_bar(TS0 + BAR, 0.60))
    assert strategy.pending_intent is not None
    assert strategy.pending_intent.action == "long_entry"
    strategy.on_bar(make_bar(TS0 + 2 * BAR, 0.60))
    assert strategy.pending_intent is None  # already above: no new cross


def test_hold_to_expiry_ignores_exit_rules(primary, complement):
    config = make_config(
        long_entry={"gt": ["close", 0.45]},
        long_exit={"lt": ["close", 0.45]},
        direction="long",
        settlement="hold_to_expiry",
    )
    strategy = make_strategy(config, primary, complement)

    strategy.on_bar(make_bar(TS0, 0.50))
    strategy.on_quote(make_quote(TS0 + BAR, bid=0.499, ask=0.501))
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=True, quantity="10.00", price="0.501", ts=TS0 + BAR)
    )
    strategy.on_bar(make_bar(TS0 + BAR, 0.40))
    assert strategy.pending_intent is None
    assert strategy.position is not None
    assert strategy.open_positions()[0]["token"] == "YES"


def test_lifecycle_filter_blocks_unknown_phase(primary, complement):
    config = make_config(
        long_entry={"gt": ["close", 0]},
        direction="long",
        lifecycle_filters=LifecycleFilter(phases=["in_play"], require_known_phase=True),
    )
    strategy = make_strategy(
        config, primary, complement, lifecycle=lambda ts: {"phase": "unknown"}
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    assert strategy.pending_intent is None
    assert strategy.diagnostics()["lifecycle_blocked_bars"] == 1


def test_lifecycle_filter_allows_known_phase(primary, complement):
    config = make_config(
        long_entry={"gt": ["close", 0]},
        direction="long",
        lifecycle_filters=LifecycleFilter(phases=["in_play"]),
    )
    strategy = make_strategy(
        config,
        primary,
        complement,
        lifecycle=lambda ts: {"phase": "in_play", "time_to_kickoff_minutes": -10},
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    assert strategy.pending_intent is not None


def test_lifecycle_callback_failure_is_treated_as_unknown(primary, complement):
    def boom(_ts):
        raise RuntimeError("no lifecycle")

    strategy = make_strategy(
        make_config(
            long_entry={"gt": ["close", 0]},
            direction="long",
            lifecycle_filters=LifecycleFilter(phases=["in_play"]),
        ),
        primary,
        complement,
        lifecycle=boom,
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    assert strategy.pending_intent is None
    assert strategy.diagnostics()["missing_lifecycle_bars"] == 1


def test_ambiguous_entries_prefer_long(primary, complement):
    config = make_config(
        long_entry={"gt": ["close", 0]},
        short_entry={"gt": ["close", 0]},
    )
    strategy = make_strategy(config, primary, complement)
    strategy.on_bar(make_bar(TS0, 0.50))
    assert strategy.pending_intent.action == "long_entry"
    assert strategy.diagnostics()["ambiguous_entries"] == 1


def test_direction_long_ignores_short_entry(primary, complement):
    config = make_config(
        long_entry={"lt": ["close", 0]},
        short_entry={"gt": ["close", 0]},
        direction="long",
    )
    strategy = make_strategy(config, primary, complement)
    strategy.on_bar(make_bar(TS0, 0.50))
    assert strategy.pending_intent is None


def test_quote_before_the_next_bar_open_is_not_used(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_bar(make_bar(TS0, 0.50))  # ready at TS0 + 60s
    strategy.on_quote(make_quote(TS0 + 1, bid=0.499, ask=0.501))  # same bar
    assert strategy.submitted == []
    assert strategy.pending_intent is not None
    strategy.on_quote(make_quote(TS0 + BAR, bid=0.499, ask=0.501))
    assert len(strategy.submitted) == 1


def test_gap_quote_is_still_the_next_available_open(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    # Sparse tape: the next available open is two bar intervals later. It is
    # still the next bar, not a late fill, and it must submit.
    strategy.on_quote(make_quote(TS0 + 2 * BAR, bid=0.499, ask=0.501))
    assert len(strategy.submitted) == 1
    assert strategy.diagnostics()["late_intents"] == 0


def test_reject_does_not_crash(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    strategy.on_order_rejected(
        SimpleNamespace(reason="No market", client_order_id="O-TEST-1")
    )
    assert strategy.diagnostics()["rejects"] == 1
    assert strategy.pending_intent is None


def test_missing_quote_price_skips_the_order(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    strategy.on_quote(make_quote(TS0 + BAR, bid=0.499, ask=0.0))
    assert strategy.submitted == []
    assert strategy.diagnostics()["missing_quote"] == 1


def test_on_stop_emits_diagnostics(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_bar(make_bar(TS0, 0.50))
    strategy.on_stop()
    snapshot = strategy.diagnostics_snapshot
    assert snapshot["bars"] == 1
    assert snapshot["signals_generated"] == 1
    assert "time_in_market_pct" in snapshot
    assert "average_time_between_signals_s" in snapshot


def test_partial_exit_records_the_original_quantity(primary, complement):
    strategy = make_strategy(
        make_config(
            long_entry={"gt": ["close", 0]},
            long_exit={"lt": ["close", 0]},
            direction="long",
        ),
        primary,
        complement,
    )
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=True, quantity="10.00", price="0.50", ts=TS0)
    )
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=False, quantity="4.00", price="0.60", ts=TS0 + BAR)
    )
    assert strategy.position.quantity == pytest.approx(6.0)
    assert strategy.trades == []
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=False, quantity="6.00", price="0.40", ts=TS0 + 2 * BAR)
    )
    assert strategy.position is None
    trade = strategy.trades[0]
    assert trade["quantity"] == pytest.approx(10.0)
    # Weighted exit: (4 * 0.60 + 6 * 0.40) / 10 = 0.48
    assert trade["exit_price"] == pytest.approx(0.48)
    assert trade["pnl_usd"] == pytest.approx((0.48 - 0.50) * 10.0)


def test_with_the_real_indicator_set(primary, complement):
    """The default factory path: real Nautilus indicators through IndicatorSet."""
    config = make_config(
        indicators=[ma_indicator(period=3)],
        long_entry={"gt": ["close", "indicator:ma:value"]},
        direction="long",
    )
    strategy = RecordingStrategy(config, primary=primary, complement=complement)

    closes = [0.40, 0.41, 0.42, 0.43]
    for index, close in enumerate(closes):
        strategy.on_bar(make_bar(TS0 + index * BAR, close))

    # SMA(3) is null on the first two bars, so only the third and fourth bars
    # can evaluate; the third closes above the SMA (0.41).
    assert [signal["ts_ns"] for signal in strategy.signals] == [
        TS0 + 2 * BAR,
        TS0 + 3 * BAR,
    ]
    assert strategy.signals[0]["indicator_values"]["ma"]["value"] == pytest.approx(0.41)
    assert strategy.diagnostics()["indicator_not_ready_bars"] == 2


def test_sell_without_a_position_is_counted_not_crashed(primary, complement):
    strategy = make_strategy(
        make_config(long_entry={"gt": ["close", 0]}, direction="long"), primary, complement
    )
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=False, quantity="1.00", price="0.50", ts=TS0)
    )
    assert strategy.diagnostics()["unexpected_fills"] == 1
    assert strategy.position is None


def test_buying_the_complement_is_not_an_exit_for_the_primary(primary, complement):
    strategy = make_strategy(
        make_config(
            long_entry={"gt": ["close", 0]},
            long_exit={"lt": ["close", 0]},
            short_entry={"lt": ["close", 0]},
            direction="both",
        ),
        primary,
        complement,
    )
    strategy.on_order_filled(
        fill_event(instrument_id=PRIMARY_ID, is_buy=True, quantity="10.00", price="0.50", ts=TS0)
    )
    assert strategy.position.token == "YES"
    # A complement fill while holding the primary is an engine anomaly, not an
    # exit: the primary position is untouched.
    strategy.on_order_filled(
        fill_event(instrument_id=COMPLEMENT_ID, is_buy=True, quantity="10.00", price="0.50", ts=TS0 + 1)
    )
    assert strategy.position.token == "YES"
    assert strategy.position.quantity == pytest.approx(10.0)
    assert strategy.diagnostics()["unexpected_fills"] == 1
