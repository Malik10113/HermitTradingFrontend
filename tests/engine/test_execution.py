from __future__ import annotations

from types import SimpleNamespace

import pytest
from nautilus_trader.model import Money, Price, Quantity

from backend.engine.execution import (
    PROFILE_TABLE,
    BookSnapshot,
    EffectiveExecution,
    NoBookProvider,
    ProfiledFeeModel,
    ProfiledFillModel,
    calculate_cost_estimate,
    clamp_price,
    profiled_fill_price,
    resolve_profile,
)

from .conftest import PRIMARY_ID, sample_instrument

TS = 1_700_000_000_000_000_000


def cfg(**overrides):
    base = {
        "profile": "conservative",
        "fee_bps": None,
        "slippage_bps": None,
        "min_abs_slippage": None,
        "book_staleness_s": 60,
        "max_depth_levels": 10,
        "fallback": "conservative",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeOrder:
    def __init__(self, *, is_buy: bool, quantity: str = "10.00", ts: int = TS) -> None:
        self.is_buy = is_buy
        self.quantity = Quantity.from_str(quantity)
        self.ts_last = ts


class Provider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def get_snapshot(self, condition_id, ts_ns):
        self.calls.append((condition_id, ts_ns))
        return self.snapshot


# ---------------------------------------------------------------------------
# Profile table and resolution


def test_profile_table_matches_the_brief():
    assert set(PROFILE_TABLE) == {
        "frictionless",
        "optimistic",
        "conservative",
        "stress",
        "book",
    }
    assert PROFILE_TABLE["frictionless"].cost_source == "none"
    assert PROFILE_TABLE["frictionless"].fee_bps == 0.0
    assert PROFILE_TABLE["frictionless"].slippage_bps == 0.0
    assert PROFILE_TABLE["frictionless"].min_abs_slippage == 0.0

    assert PROFILE_TABLE["optimistic"].cost_source == "fixed"
    assert PROFILE_TABLE["optimistic"].slippage_bps == 0.0
    assert PROFILE_TABLE["optimistic"].min_abs_slippage == 0.0

    assert PROFILE_TABLE["conservative"].slippage_bps == 10.0
    assert PROFILE_TABLE["conservative"].min_abs_slippage == 0.001
    assert PROFILE_TABLE["stress"].slippage_bps == 25.0
    assert PROFILE_TABLE["stress"].min_abs_slippage == 0.002

    assert PROFILE_TABLE["book"].cost_source == "dynamic"
    assert PROFILE_TABLE["book"].max_depth_levels == 10
    assert PROFILE_TABLE["book"].fallback == "conservative"


@pytest.mark.parametrize(
    ("profile", "effective", "cost_source"),
    [
        ("frictionless", "frictionless", "none"),
        ("optimistic", "optimistic", "fixed"),
        ("conservative", "conservative", "fixed"),
        ("stress", "stress", "fixed"),
    ],
)
def test_fixed_profiles_resolve_without_fallback(profile, effective, cost_source):
    resolved = resolve_profile(cfg(profile=profile), has_book_coverage=False)
    assert resolved.requested == profile
    assert resolved.effective == effective
    assert resolved.cost_source == cost_source
    assert resolved.fallback_reason is None


def test_venue_fee_defaults_to_zero_but_explicit_fee_is_preserved():
    resolved = resolve_profile(cfg(profile="conservative"), has_book_coverage=False)
    assert resolved.fee_bps == 0.0

    resolved = resolve_profile(
        cfg(profile="conservative", fee_bps=250), has_book_coverage=False, venue_fee_bps=100
    )
    assert resolved.fee_bps == 250.0

    resolved = resolve_profile(
        cfg(profile="conservative"), has_book_coverage=False, venue_fee_bps=100
    )
    assert resolved.fee_bps == 100.0


def test_book_without_coverage_is_priced_as_conservative():
    resolved = resolve_profile(
        cfg(profile="book"), has_book_coverage=False, coverage_window=(TS, TS + 10)
    )
    assert resolved.requested == "book"
    assert resolved.effective == "conservative"
    assert resolved.cost_source == "fixed"
    assert resolved.fallback_reason == "no_book_coverage"
    assert resolved.slippage_bps == 10.0
    assert resolved.min_abs_slippage == 0.001
    assert resolved.book_walk is False
    assert resolved.coverage == (TS, TS + 10)


def test_book_with_coverage_stays_dynamic():
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    assert resolved.effective == "book"
    assert resolved.cost_source == "dynamic"
    assert resolved.book_walk is True
    assert resolved.fallback_reason is None
    assert resolved.max_depth_levels == 10


@pytest.mark.parametrize(
    "bad",
    [
        {"profile": "magic"},
        {"profile": "conservative", "fee_bps": -1},
        {"profile": "conservative", "slippage_bps": -0.5},
        {"profile": "frictionless", "slippage_bps": 10},
        {"profile": "frictionless", "fee_bps": 5},
        {"profile": "frictionless", "min_abs_slippage": 0.001},
        {"profile": "book", "slippage_bps": 10},
        {"profile": "book", "min_abs_slippage": 0.001},
        {"profile": "book", "max_depth_levels": 0},
        {"profile": "book", "book_staleness_s": -1},
        {"profile": "book", "fallback": "frictionless"},
    ],
)
def test_invalid_profile_parameters_are_rejected(bad):
    with pytest.raises(ValueError):
        resolve_profile(cfg(**bad), has_book_coverage=True)


def test_fill_price_formula_and_clamping():
    # 10 bps of 0.42 is 0.00042, below the 0.001 absolute minimum.
    buy = profiled_fill_price(
        0.42, is_buy=True, slippage_bps=10, min_abs_slippage=0.001, tick=0.001
    )
    sell = profiled_fill_price(
        0.42, is_buy=False, slippage_bps=10, min_abs_slippage=0.001, tick=0.001
    )
    assert buy == pytest.approx(0.421)
    assert sell == pytest.approx(0.419)

    # 25 bps of 0.42 is 0.00105, above the 0.002 minimum? No: 0.00105 < 0.002.
    stress_buy = profiled_fill_price(
        0.42, is_buy=True, slippage_bps=25, min_abs_slippage=0.002, tick=0.001
    )
    assert stress_buy == pytest.approx(0.422)

    # Clamps: never outside [tick, 1 - tick].
    assert profiled_fill_price(
        0.999, is_buy=True, slippage_bps=100, min_abs_slippage=0.01, tick=0.001
    ) == pytest.approx(0.999)
    assert profiled_fill_price(
        0.001, is_buy=False, slippage_bps=100, min_abs_slippage=0.01, tick=0.001
    ) == pytest.approx(0.001)
    assert clamp_price(1.5, 0.001) == 0.999
    assert clamp_price(-1.0, 0.001) == 0.001


def test_cost_estimate_is_additive_and_honest():
    resolved = resolve_profile(cfg(profile="conservative"), has_book_coverage=False)
    estimate = calculate_cost_estimate(0.42, resolved, 100)

    assert estimate["fee_bps"] == 0.0
    assert estimate["slip_price_points"] == pytest.approx(0.001)
    assert estimate["buy_price"] == pytest.approx(0.421)
    assert estimate["sell_price"] == pytest.approx(0.419)
    # 0.001 / 0.42 = 23.8 bps, because the absolute minimum dominates.
    assert estimate["slippage_bps"] == pytest.approx(23.8095, abs=1e-4)
    assert estimate["total_cost_bps"] == pytest.approx(23.8095, abs=1e-4)

    frictionless = resolve_profile(cfg(profile="frictionless"), has_book_coverage=False)
    zero = calculate_cost_estimate(0.42, frictionless, 100)
    assert zero["total_cost_bps"] == 0.0
    assert zero["buy_price"] == zero["sell_price"] == 0.42

    book = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    dynamic = calculate_cost_estimate(0.42, book, 100)
    assert dynamic["slippage_bps"] is None
    assert dynamic["buy_price"] is None
    assert dynamic["book_walk_required"] is True
    # Fees are still known; no price impact is invented for the walk, and the
    # total is None rather than 0 so the UI cannot read it as "zero cost".
    assert dynamic["fee_only_bps"] == 0.0
    assert dynamic["total_cost_bps"] is None
    assert dynamic["note"]


def test_cost_estimate_rejects_bad_inputs():
    resolved = resolve_profile(cfg(), has_book_coverage=False)
    with pytest.raises(ValueError):
        calculate_cost_estimate(1.2, resolved, 10)
    with pytest.raises(ValueError):
        calculate_cost_estimate(0.42, resolved, 0)


# ---------------------------------------------------------------------------
# Fee model


def test_fee_model_charges_bps_of_notional_in_usd(primary):
    model = ProfiledFeeModel(10)
    fee = model.get_commission(None, Quantity.from_str("100.00"), Price.from_str("0.430"), primary)
    assert isinstance(fee, Money)
    assert str(fee.currency) == "USD"
    assert float(fee) == pytest.approx(0.04)  # 100 * 0.43 * 10 / 10_000
    assert model.total_fee_usd == pytest.approx(0.04)

    tiny = ProfiledFeeModel(5).get_commission(
        None, Quantity.from_str("3.00"), Price.from_str("0.333"), primary
    )
    assert float(tiny) == 0.0


def test_zero_fee_model_returns_zero(primary):
    model = ProfiledFeeModel(0)
    fee = model.get_commission(None, Quantity.from_str("10.00"), Price.from_str("0.500"), primary)
    assert float(fee) == 0.0


# ---------------------------------------------------------------------------
# Fill model


def test_fill_model_builds_synthetic_l1_at_the_profiled_price(primary):
    resolved = resolve_profile(cfg(profile="conservative"), has_book_coverage=False)
    model = ProfiledFillModel(resolved)

    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.419"), Price.from_str("0.421")
    )
    assert book is not None
    # ref = best ask 0.421; 0.421 + max(0.000421, 0.001) = 0.422
    assert float(book.best_ask_price()) == pytest.approx(0.422)
    assert float(book.best_bid_price()) == pytest.approx(0.422)
    assert float(book.best_ask_size()) == pytest.approx(10.0)
    assert model.last_fill_source == "fixed"
    assert model.last_fill_price == pytest.approx(0.422)
    assert model.last_spread_cost == pytest.approx(0.001)
    assert model.fallback_rate == 0.0


def test_fill_model_sell_uses_bid_minus_slippage(primary):
    resolved = resolve_profile(cfg(profile="stress"), has_book_coverage=False)
    model = ProfiledFillModel(resolved)
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=False), Price.from_str("0.419"), Price.from_str("0.421")
    )
    assert float(book.best_bid_price()) == pytest.approx(0.417)
    assert model.last_fill_source == "fixed"
    assert model.last_reference_price == pytest.approx(0.419)


def test_frictionless_fill_has_no_cost(primary):
    resolved = resolve_profile(cfg(profile="frictionless"), has_book_coverage=False)
    model = ProfiledFillModel(resolved)
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.419"), Price.from_str("0.421")
    )
    assert float(book.best_ask_price()) == pytest.approx(0.421)
    assert model.last_fill_source == "frictionless"
    assert model.last_spread_cost == pytest.approx(0.0)


def test_fill_model_falls_back_to_the_other_side_when_one_is_missing(primary):
    resolved = resolve_profile(cfg(profile="conservative"), has_book_coverage=False)
    model = ProfiledFillModel(resolved)
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.419"), None
    )
    assert book is not None
    assert model.last_reference_price == pytest.approx(0.419)


def test_fill_model_returns_none_without_any_reference_price(primary):
    resolved = resolve_profile(cfg(profile="conservative"), has_book_coverage=False)
    model = ProfiledFillModel(resolved)
    assert model.get_orderbook_for_fill_simulation(primary, FakeOrder(is_buy=True), None, None) is None
    assert model.last_fallback_reason == "no_reference_price"


def test_book_fill_walks_three_levels(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.400, 5.0), (0.390, 5.0)],
        asks=[(0.420, 2.0), (0.430, 3.0), (0.440, 10.0)],
    )
    provider = Provider(snapshot)
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, provider, "0xabc")

    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True, quantity="4.00"), Price.from_str("0.400"), Price.from_str("0.420")
    )

    # (2 @ 0.42 + 2 @ 0.43) / 4 = 0.425
    assert float(book.best_ask_price()) == pytest.approx(0.425)
    assert model.last_fill_source == "book"
    assert model.last_filled_quantity == pytest.approx(4.0)
    assert model.last_unfilled_quantity == 0.0
    assert model.last_levels_walked == 2
    assert model.last_fill_price == pytest.approx(0.425)
    # mid = (0.40 + 0.42) / 2 = 0.41
    assert model.last_impact_bps == pytest.approx((0.425 - 0.41) / 0.41 * 10_000, abs=1e-3)
    assert model.last_spread_cost == pytest.approx(0.005)
    assert provider.calls == [("0xabc", TS)]
    assert model.book_fills == 1


def test_book_walk_on_the_bid_side(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.400, 5.0), (0.390, 2.0)],
        asks=[(0.420, 1.0)],
    )
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")

    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=False, quantity="6.00"), Price.from_str("0.400"), Price.from_str("0.420")
    )
    expected = (5 * 0.400 + 1 * 0.390) / 6
    # The fill is rounded to the instrument's price precision (0.001).
    assert float(book.best_bid_price()) == pytest.approx(
        float(primary.make_price(expected))
    )
    assert model.last_fill_source == "book"
    assert model.last_spread_cost == pytest.approx(0.400 - expected, abs=5e-4)
    assert model.last_impact_bps > 0


def test_partial_depth_leaves_the_remainder_unfilled(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.400, 1.0)],
        asks=[(0.420, 1.0)],
    )
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")

    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True, quantity="5.00"), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert float(book.best_ask_size()) == pytest.approx(1.0)
    assert model.last_filled_quantity == pytest.approx(1.0)
    assert model.last_unfilled_quantity == pytest.approx(4.0)


def test_max_depth_levels_caps_the_walk(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.400, 1.0)],
        asks=[(0.410, 1.0), (0.420, 1.0), (0.430, 1.0)],
    )
    resolved = resolve_profile(cfg(profile="book", max_depth_levels=2), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True, quantity="3.00"), Price.from_str("0.400"), Price.from_str("0.410")
    )
    assert model.last_levels_walked == 2
    assert model.last_filled_quantity == pytest.approx(2.0)
    assert model.last_unfilled_quantity == pytest.approx(1.0)
    assert float(book.best_ask_price()) == pytest.approx(0.415)


def test_stale_snapshot_falls_back_to_conservative(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 120 * 10**9,
        bids=[(0.400, 5.0)],
        asks=[(0.420, 5.0)],
    )
    resolved = resolve_profile(
        cfg(profile="book", book_staleness_s=60), has_book_coverage=True
    )
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert book is not None
    assert model.last_fill_source == "conservative_fallback"
    assert model.last_fallback_reason == "book_snapshot_stale"
    assert float(book.best_ask_price()) == pytest.approx(0.421)  # 0.42 + 0.001
    assert model.fallback_fills == 1
    assert model.fallback_rate == pytest.approx(1.0)


def test_missing_snapshot_falls_back_to_conservative(primary):
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, NoBookProvider(), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert model.last_fill_source == "conservative_fallback"
    assert model.last_fallback_reason == "no_book_snapshot"
    assert float(book.best_ask_price()) == pytest.approx(0.421)


def test_future_snapshot_never_prices_a_fill(primary):
    # Leakage guard: a snapshot after the decision time must never be chosen.
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS + 1,
        bids=[(0.400, 5.0)],
        asks=[(0.420, 5.0)],
    )
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert model.last_fill_source == "conservative_fallback"
    assert model.last_fallback_reason == "book_snapshot_after_decision"
    assert float(book.best_ask_price()) == pytest.approx(0.421)


def test_book_fill_fails_closed_when_decision_timestamp_is_zero(primary):
    # Without a decision timestamp the guard cannot prove the snapshot is in
    # the past, so even a snapshot that looks future-dated must not price it.
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS + 1,
        bids=[(0.400, 5.0)],
        asks=[(0.420, 5.0)],
    )
    provider = Provider(snapshot)
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, provider, "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True, ts=0), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert book is not None
    assert model.last_fill_source == "conservative_fallback"
    assert model.last_fallback_reason == "missing_decision_timestamp"
    assert provider.calls == []
    assert float(book.best_ask_price()) == pytest.approx(0.421)


def test_book_fill_passes_instrument_id_to_token_aware_provider(primary, complement):
    class TokenAwareProvider:
        def __init__(self, snapshot):
            self.snapshot = snapshot
            self.calls = []

        def get_snapshot(self, condition_id, ts_ns, instrument_id=None):
            self.calls.append((condition_id, ts_ns, instrument_id))
            return self.snapshot

    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.590, 5.0)],
        asks=[(0.600, 5.0)],
    )
    provider = TokenAwareProvider(snapshot)
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, provider, "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        complement, FakeOrder(is_buy=True), Price.from_str("0.590"), Price.from_str("0.600")
    )
    assert model.last_fill_source == "book"
    assert provider.calls == [("0xabc", TS, complement.id.value)]
    assert float(book.best_ask_price()) == pytest.approx(0.600)


def test_two_argument_provider_still_works(primary):
    snapshot = BookSnapshot(
        condition_id="0xabc",
        ts_ns=TS - 1,
        bids=[(0.400, 5.0)],
        asks=[(0.420, 5.0)],
    )
    provider = Provider(snapshot)
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, provider, "0xabc")
    model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert model.last_fill_source == "book"
    assert provider.calls == [("0xabc", TS)]


def test_provider_error_falls_back_without_raising(primary):
    class Exploding:
        def get_snapshot(self, condition_id, ts_ns):
            raise RuntimeError("boom")

    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Exploding(), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert book is not None
    assert model.last_fallback_reason == "book_snapshot_error"


def test_book_snapshot_may_be_a_mapping(primary):
    # The v2 book replay seam may hand back plain dicts rather than BookSnapshot.
    snapshot = {
        "condition_id": "0xabc",
        "ts_ns": TS - 1,
        "bids": [{"price": 0.400, "size": 5.0}],
        "asks": [{"price": 0.420, "size": 5.0}],
    }
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=True)
    model = ProfiledFillModel(resolved, Provider(snapshot), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert model.last_fill_source == "book"
    assert float(book.best_ask_price()) == pytest.approx(0.420)


def test_book_run_level_fallback_uses_fixed_conservative_costs(primary):
    resolved = resolve_profile(cfg(profile="book"), has_book_coverage=False)
    model = ProfiledFillModel(resolved, Provider(None), "0xabc")
    book = model.get_orderbook_for_fill_simulation(
        primary, FakeOrder(is_buy=True), Price.from_str("0.400"), Price.from_str("0.420")
    )
    assert model.last_fill_source == "fixed"
    assert model.last_fallback_reason is None
    assert float(book.best_ask_price()) == pytest.approx(0.421)


def test_fill_model_never_raises_on_malformed_order(primary):
    resolved = resolve_profile(cfg(), has_book_coverage=False)
    model = ProfiledFillModel(resolved)

    class Broken:
        is_buy = True
        quantity = Quantity.from_str("1.00")
        ts_last = TS

    class BrokenInstrument:
        id = PRIMARY_ID
        price_increment = None
        price_precision = "nope"

        def make_price(self, value):
            raise RuntimeError("no")

        def make_qty(self, value, round_down=True):
            raise RuntimeError("no")

    assert model.get_orderbook_for_fill_simulation(BrokenInstrument(), Broken(), None, None) is None
    assert model.last_fallback_reason is not None


def test_effective_execution_manifest_shape():
    resolved = resolve_profile(
        cfg(profile="book", fee_bps=50), has_book_coverage=False, coverage_window=(1, 2)
    )
    manifest = resolved.to_manifest()
    assert isinstance(resolved, EffectiveExecution)
    assert manifest["requested"] == "book"
    assert manifest["effective"] == "conservative"
    assert manifest["fallback_reason"] == "no_book_coverage"
    assert manifest["book_coverage"] == {"start_ns": 1, "end_ns": 2}
    assert manifest["fee_bps"] == 50.0
