"""Execution profiles: costs, fees and the synthetic L1 fill model.

One user-facing vocabulary of five profiles (BRIEF §8.4).  Each resolves to an
internal cost source (``none``, ``fixed``, ``dynamic``) that is recorded in the
manifest but never shown as a second set of labels.

    slip      = max(c * slippage_bps / 10_000, min_abs_slippage)
    buy_fill  = clamp(c + slip, tick, 1 - tick)
    sell_fill = clamp(c - slip, tick, 1 - tick)

The formula applies to YES and NO alike: each token is its own instrument and
the side comes from the open/close intent.

Book is dynamic: it walks the latest L2 snapshot with ``ts <= decision_ts``,
never a later one, and falls back to Conservative per fill when the snapshot is
missing or stale.  A run with no book coverage keeps ``requested: book`` but is
priced as ``effective: conservative`` with ``reason: no_book_coverage``.  It
never falls back to Frictionless.

Everything here is read-only estimation.  No value is a profitability claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from nautilus_trader.execution import FeeModel, FillModel
from nautilus_trader.model import (
    BookOrder,
    BookType,
    Currency,
    Money,
    OrderBook,
    OrderSide,
    Price,
    Quantity,
)

_USD = Currency.from_str("USD")

PROFILES = ("frictionless", "optimistic", "conservative", "stress", "book")
DEFAULT_TICK = 0.001


@dataclass(frozen=True)
class ProfileSpec:
    """Static definition of one execution profile."""

    name: str
    cost_source: str  # none | fixed | dynamic
    fee_bps: float | None  # None = venue schedule (configurable)
    slippage_bps: float
    min_abs_slippage: float
    book_walk: bool = False
    fallback: str | None = None
    max_depth_levels: int = 10
    book_staleness_s: int = 60


PROFILE_TABLE: dict[str, ProfileSpec] = {
    "frictionless": ProfileSpec("frictionless", "none", 0.0, 0.0, 0.0),
    "optimistic": ProfileSpec("optimistic", "fixed", None, 0.0, 0.0),
    "conservative": ProfileSpec("conservative", "fixed", None, 10.0, 0.001),
    "stress": ProfileSpec("stress", "fixed", None, 25.0, 0.002),
    "book": ProfileSpec(
        "book",
        "dynamic",
        None,
        0.0,
        0.0,
        book_walk=True,
        fallback="conservative",
        max_depth_levels=10,
        book_staleness_s=60,
    ),
}

CONSERVATIVE = PROFILE_TABLE["conservative"]


@dataclass(frozen=True)
class EffectiveExecution:
    """The resolved cost model for one run (recorded in the manifest)."""

    requested: str
    effective: str
    cost_source: str
    fee_bps: float
    slippage_bps: float
    min_abs_slippage: float
    book_walk: bool = False
    fallback: str | None = None
    fallback_reason: str | None = None
    coverage: tuple[int, int] | None = None
    book_staleness_s: int = 60
    max_depth_levels: int = 10

    def to_manifest(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "effective": self.effective,
            "cost_source": self.cost_source,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "min_abs_slippage": self.min_abs_slippage,
            "book_walk": self.book_walk,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "book_coverage": (
                {"start_ns": self.coverage[0], "end_ns": self.coverage[1]}
                if self.coverage is not None
                else None
            ),
            "book_staleness_s": self.book_staleness_s,
            "max_depth_levels": self.max_depth_levels,
        }


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _validate_non_negative(name: str, value: Any) -> float:
    """Validate a finite non-negative number, raising with the field name."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    number = float(value)
    if number != number or number < 0.0 or number == float("inf"):
        raise ValueError(f"{name} must be a finite non-negative number, got {value!r}")
    return number


def resolve_profile(
    execution_cfg: Any,
    has_book_coverage: bool,
    coverage_window: tuple[int, int] | Sequence[int] | None = None,
    *,
    venue_fee_bps: float = 0.0,
    default_max_depth_levels: int = 10,
    default_book_staleness_s: int = 60,
) -> EffectiveExecution:
    """Resolve a run's requested profile to its effective cost model.

    Explicit ``fee_bps`` / ``slippage_bps`` / ``min_abs_slippage`` values in the
    ``execution`` block override the profile defaults.  Invalid combinations
    raise ``ValueError``; a run is never downgraded to Frictionless.
    """
    profile = str(_cfg_get(execution_cfg, "profile", "conservative"))
    if profile not in PROFILE_TABLE:
        raise ValueError(
            f"unknown execution profile {profile!r}; expected one of {list(PROFILES)}"
        )
    spec = PROFILE_TABLE[profile]

    venue_fee = _validate_non_negative("venue_fee_bps", venue_fee_bps)
    explicit_fee = _cfg_get(execution_cfg, "fee_bps")
    fee_bps = venue_fee if explicit_fee is None else _validate_non_negative("fee_bps", explicit_fee)

    explicit_slip = _cfg_get(execution_cfg, "slippage_bps")
    explicit_min_abs = _cfg_get(execution_cfg, "min_abs_slippage")

    if profile == "frictionless":
        if explicit_fee is not None and float(explicit_fee) != 0.0:
            raise ValueError("frictionless profile requires fee_bps = 0")
        if explicit_slip not in (None, 0, 0.0):
            raise ValueError("frictionless profile requires slippage_bps = 0")
        if explicit_min_abs not in (None, 0, 0.0):
            raise ValueError("frictionless profile requires min_abs_slippage = 0")
        slippage_bps = 0.0
        min_abs_slippage = 0.0
        fee_bps = 0.0
    elif profile == "book":
        if explicit_slip not in (None, 0, 0.0) or explicit_min_abs not in (None, 0, 0.0):
            raise ValueError(
                "book profile prices slippage from the book walk; "
                "slippage_bps and min_abs_slippage must be left unset"
            )
        slippage_bps = _validate_non_negative(
            "slippage_bps",
            explicit_slip if explicit_slip is not None else spec.slippage_bps,
        )
        min_abs_slippage = _validate_non_negative(
            "min_abs_slippage",
            explicit_min_abs if explicit_min_abs is not None else spec.min_abs_slippage,
        )
    else:
        slippage_bps = _validate_non_negative(
            "slippage_bps",
            explicit_slip if explicit_slip is not None else spec.slippage_bps,
        )
        min_abs_slippage = _validate_non_negative(
            "min_abs_slippage",
            explicit_min_abs if explicit_min_abs is not None else spec.min_abs_slippage,
        )

    depth_raw = _cfg_get(execution_cfg, "max_depth_levels")
    max_depth_levels = int(
        default_max_depth_levels if depth_raw is None else depth_raw
    )
    if max_depth_levels < 1:
        raise ValueError(f"max_depth_levels must be >= 1, got {max_depth_levels}")
    staleness_raw = _cfg_get(execution_cfg, "book_staleness_s")
    staleness_s = int(
        default_book_staleness_s if staleness_raw is None else staleness_raw
    )
    if staleness_s < 0:
        raise ValueError(f"book_staleness_s must be >= 0, got {staleness_s}")
    fallback = _cfg_get(execution_cfg, "fallback", "conservative")
    if fallback not in (None, "conservative"):
        raise ValueError(
            f"book fallback must be conservative, got {fallback!r}"
        )

    coverage: tuple[int, int] | None = None
    if coverage_window is not None:
        try:
            start_ns, end_ns = (int(coverage_window[0]), int(coverage_window[1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"coverage_window must be (start_ns, end_ns), got {coverage_window!r}") from exc
        if start_ns > end_ns:
            raise ValueError("coverage_window start must be <= end")
        coverage = (start_ns, end_ns)

    if profile != "book":
        return EffectiveExecution(
            requested=profile,
            effective=profile,
            cost_source=spec.cost_source,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            min_abs_slippage=min_abs_slippage,
            book_walk=False,
            fallback=None,
            fallback_reason=None,
            coverage=coverage,
            book_staleness_s=staleness_s,
            max_depth_levels=max_depth_levels,
        )

    if not has_book_coverage:
        return EffectiveExecution(
            requested="book",
            effective="conservative",
            cost_source="fixed",
            fee_bps=fee_bps,
            slippage_bps=CONSERVATIVE.slippage_bps,
            min_abs_slippage=CONSERVATIVE.min_abs_slippage,
            book_walk=False,
            fallback="conservative",
            fallback_reason="no_book_coverage",
            coverage=coverage,
            book_staleness_s=staleness_s,
            max_depth_levels=max_depth_levels,
        )

    return EffectiveExecution(
        requested="book",
        effective="book",
        cost_source="dynamic",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        min_abs_slippage=min_abs_slippage,
        book_walk=True,
        fallback="conservative",
        fallback_reason=None,
        coverage=coverage,
        book_staleness_s=staleness_s,
        max_depth_levels=max_depth_levels,
    )


# ---------------------------------------------------------------------------
# Price arithmetic


def clamp_price(price: float, tick: float) -> float:
    """Clamp to ``[tick, 1 - tick]`` (both bounds inclusive)."""
    if tick <= 0.0 or tick >= 0.5:
        raise ValueError(f"tick must be in (0, 0.5), got {tick!r}")
    return max(tick, min(1.0 - tick, price))


def profiled_fill_price(
    reference_price: float,
    *,
    is_buy: bool,
    slippage_bps: float,
    min_abs_slippage: float,
    tick: float,
) -> float:
    """``c ± slip`` with ``slip = max(c * bps / 10_000, min_abs)``, clamped."""
    reference = float(reference_price)
    if reference != reference or not (0.0 < reference < 1.0):
        raise ValueError(f"reference price must be in (0, 1), got {reference_price!r}")
    slip = max(reference * float(slippage_bps) / 10_000.0, float(min_abs_slippage))
    return clamp_price(reference + slip if is_buy else reference - slip, tick)


def calculate_cost_estimate(
    reference_price: float,
    execution: EffectiveExecution,
    quantity: float,
    *,
    tick_size: float = DEFAULT_TICK,
) -> dict[str, Any]:
    """Per-side cost estimate for preflight; never a profitability claim.

    ``total_cost_bps`` is fees plus the effective slippage in bps (which can
    exceed the configured bps when the absolute minimum dominates at low
    prices).  For a dynamic (book) run the walk is not known before execution,
    so the price fields are ``None`` and ``book_walk_required`` is true; the
    fee component is still reported.
    """
    reference = float(reference_price)
    if not (0.0 < reference < 1.0):
        raise ValueError(f"reference_price must be in (0, 1), got {reference_price!r}")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or float(quantity) <= 0:
        raise ValueError(f"quantity must be a positive number, got {quantity!r}")

    fee_bps = float(execution.fee_bps)
    if execution.cost_source == "dynamic":
        # The book walk is not known before execution. Reporting a total here
        # would read as "zero cost"; report the fee component only and mark the
        # slippage/impact as unavailable.
        return {
            "requested": execution.requested,
            "effective": execution.effective,
            "cost_source": execution.cost_source,
            "reference_price": reference,
            "quantity": float(quantity),
            "fee_bps": fee_bps,
            "slippage_bps": None,
            "slip_price_points": None,
            "buy_price": None,
            "sell_price": None,
            "total_cost_bps": None,
            "fee_only_bps": fee_bps,
            "book_walk_required": True,
            "note": "Book walk required; fee reported, slippage and impact are not known before execution.",
        }

    tick = float(tick_size)
    slip = max(reference * float(execution.slippage_bps) / 10_000.0, float(execution.min_abs_slippage))
    buy_price = clamp_price(reference + slip, tick)
    sell_price = clamp_price(reference - slip, tick)
    effective_slip_bps = (slip / reference) * 10_000.0
    return {
        "requested": execution.requested,
        "effective": execution.effective,
        "cost_source": execution.cost_source,
        "reference_price": reference,
        "quantity": float(quantity),
        "fee_bps": fee_bps,
        "slippage_bps": round(effective_slip_bps, 4),
        "configured_slippage_bps": float(execution.slippage_bps),
        "slip_price_points": round(slip, 6),
        "buy_price": round(buy_price, 6),
        "sell_price": round(sell_price, 6),
        "total_cost_bps": round(fee_bps + effective_slip_bps, 4),
        "book_walk_required": False,
    }


# ---------------------------------------------------------------------------
# Fee model


class ProfiledFeeModel(FeeModel):
    """Charges ``fee_bps`` of traded notional in the instrument's currency."""

    def __init__(self, fee_bps: float) -> None:
        super().__init__()
        self.fee_bps = _validate_non_negative("fee_bps", fee_bps)
        self.total_fee_usd = 0.0
        self.fee_events = 0

    def get_commission(
        self,
        order: Any,
        fill_quantity: Quantity,
        fill_px: Price,
        instrument: Any,
    ) -> Money:
        try:
            qty = float(fill_quantity)
            px = float(fill_px)
            currency = instrument.currency
        except Exception:  # noqa: BLE001 - a fee must never abort a fill
            return Money(0.0, _USD)
        fee = Decimal(str(qty)) * Decimal(str(px)) * Decimal(str(self.fee_bps)) / Decimal(10_000)
        fee = fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if fee < 0:
            fee = Decimal("0.00")
        self.total_fee_usd += float(fee)
        self.fee_events += 1
        return Money(float(fee), currency)


# ---------------------------------------------------------------------------
# Book snapshots


@dataclass(frozen=True)
class BookSnapshot:
    """One L2 snapshot as consumed by the fill model.

    ``bids`` / ``asks`` are ``(price, size)`` pairs in any order; the fill model
    sorts them itself.  ``ts_ns`` is the snapshot time and is compared against
    the order's decision time.
    """

    condition_id: str | None
    ts_ns: int
    bids: Sequence[Any] = field(default_factory=tuple)
    asks: Sequence[Any] = field(default_factory=tuple)


@runtime_checkable
class BookSnapshotProvider(Protocol):
    """Injectable snapshot source; the v1 default has no book coverage."""

    def get_snapshot(self, condition_id: str, ts_ns: int) -> BookSnapshot | None: ...


class NoBookProvider:
    """Default v1 provider: no L2 snapshots exist for the run."""

    def get_snapshot(self, condition_id: str, ts_ns: int) -> BookSnapshot | None:
        return None


def _level_pair(level: Any) -> tuple[float, float] | None:
    """Normalise a book level to ``(price, size)``; ``None`` when malformed."""
    try:
        if isinstance(level, Mapping):
            price = level.get("price", level.get("px"))
            size = level.get("size", level.get("qty"))
        elif isinstance(level, (tuple, list)):
            if len(level) < 2:
                return None
            price, size = level[0], level[1]
        else:
            price = getattr(level, "price", None)
            size = getattr(level, "size", None)
        price_f = float(price() if callable(price) else price)
        size_f = float(size() if callable(size) else size)
    except (TypeError, ValueError):
        return None
    if price_f != price_f or size_f != size_f or size_f <= 0.0:
        return None
    if not (0.0 < price_f < 1.0):
        return None
    return price_f, size_f


def _snapshot_field(snapshot: Any, name: str) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(name)
    return getattr(snapshot, name, None)


def _sorted_levels(raw: Sequence[Any], *, is_bid: bool) -> list[tuple[float, float]]:
    levels = [pair for pair in (_level_pair(level) for level in raw or ()) if pair is not None]
    levels.sort(key=lambda pair: pair[0], reverse=is_bid)
    return levels


# ---------------------------------------------------------------------------
# Fill model


class ProfiledFillModel(FillModel):
    """Synthetic L1 order book at the profiled fill price.

    Fixed profiles place the fill at ``c ± slip`` with depth equal to the order
    quantity, so a market order fills completely at one deterministic price.

    The Book profile walks the latest snapshot at or before the decision time.
    Depth is capped at the visible quantity: the returned book is only as deep
    as the walk, so Nautilus leaves the remainder unfilled.  A missing, stale or
    future snapshot prices that fill at Conservative and records the reason.
    """

    def __init__(
        self,
        execution: EffectiveExecution,
        snapshot_provider: BookSnapshotProvider | None = None,
        condition_id: str | None = None,
        *,
        tick_size: float | None = None,
    ) -> None:
        super().__init__()
        self.execution = execution
        self.snapshot_provider = snapshot_provider
        self.condition_id = condition_id
        self.tick_size = tick_size
        self._fills = 0
        self._book_fills = 0
        self._fallback_fills = 0
        self._reset_fill_state()

    # -- diagnostics ------------------------------------------------------

    def _reset_fill_state(self) -> None:
        self.last_fill_source: str | None = None
        self.last_fallback_reason: str | None = None
        self.last_impact_bps: float | None = None
        self.last_spread_cost: float | None = None
        self.last_reference_price: float | None = None
        self.last_fill_price: float | None = None
        self.last_requested_quantity: float = 0.0
        self.last_filled_quantity: float = 0.0
        self.last_unfilled_quantity: float = 0.0
        self.last_levels_walked: int = 0

    @property
    def fills(self) -> int:
        return self._fills

    @property
    def fallback_fills(self) -> int:
        return self._fallback_fills

    @property
    def book_fills(self) -> int:
        return self._book_fills

    @property
    def fallback_rate(self) -> float:
        return (self._fallback_fills / self._fills) if self._fills else 0.0

    def diagnostics(self) -> dict[str, Any]:
        return {
            "fills": self._fills,
            "book_fills": self._book_fills,
            "fallback_fills": self._fallback_fills,
            "fallback_rate": self.fallback_rate,
            "last_fill_source": self.last_fill_source,
            "last_fallback_reason": self.last_fallback_reason,
            "last_impact_bps": self.last_impact_bps,
            "last_spread_cost": self.last_spread_cost,
        }

    # -- FillModel --------------------------------------------------------

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Any,
        order: Any,
        best_bid: Price | None = None,
        best_ask: Price | None = None,
    ) -> OrderBook | None:
        # A raise here aborts the fill silently in the engine; never raise.
        try:
            return self._build_book(instrument, order, best_bid, best_ask)
        except Exception:  # noqa: BLE001 - see docs/IMPLEMENTATION.md
            self.last_fill_source = "error"
            self.last_fallback_reason = "fill_model_error"
            return None

    # -- internals --------------------------------------------------------

    def _build_book(
        self,
        instrument: Any,
        order: Any,
        best_bid: Price | None,
        best_ask: Price | None,
    ) -> OrderBook | None:
        self._reset_fill_state()
        is_buy = bool(order.is_buy)
        reference = best_ask if is_buy else best_bid
        other = best_bid if is_buy else best_ask
        if reference is None:
            reference = other
        if reference is None:
            self.last_fallback_reason = "no_reference_price"
            return None
        ref = float(reference)
        if not (0.0 < ref < 1.0):
            self.last_fallback_reason = "invalid_reference_price"
            return None

        quantity = float(order.quantity)
        if quantity <= 0.0:
            self.last_fallback_reason = "non_positive_quantity"
            return None
        self.last_requested_quantity = quantity
        self.last_reference_price = ref

        tick = self._tick_for(instrument)
        decision_ts = int(getattr(order, "ts_last", 0) or 0)

        fill_px: float | None = None
        depth = quantity
        source = "fixed"
        reason: str | None = None

        if self.execution.book_walk:
            walked = self._walk_book(instrument, order, is_buy, ref, quantity, tick, decision_ts)
            if walked is not None:
                fill_px, depth, source, reason = walked
            else:
                fill_px, reason = None, self.last_fallback_reason

        if fill_px is None:
            # Conservative per-fill fallback; never Frictionless.
            if self.execution.book_walk:
                source = "conservative_fallback"
                self._fallback_fills += 1
                self.last_fallback_reason = reason or "book_unavailable"
            else:
                source = {
                    "none": "frictionless",
                    "fixed": "fixed",
                }.get(self.execution.cost_source, "fixed")
                self.last_fallback_reason = None
            fill_px = profiled_fill_price(
                ref,
                is_buy=is_buy,
                slippage_bps=self.execution.slippage_bps
                if not self.execution.book_walk
                else CONSERVATIVE.slippage_bps,
                min_abs_slippage=self.execution.min_abs_slippage
                if not self.execution.book_walk
                else CONSERVATIVE.min_abs_slippage,
                tick=tick,
            )
            impact = None
            spread_cost = fill_px - ref if is_buy else ref - fill_px
        else:
            impact = self.last_impact_bps
            spread_cost = self.last_spread_cost

        price = self._make_price(instrument, fill_px, tick)
        if price is None:
            self.last_fallback_reason = self.last_fallback_reason or "price_not_representable"
            return None

        qty = self._make_quantity(instrument, depth)
        if qty is None:
            self.last_fallback_reason = self.last_fallback_reason or "depth_below_increment"
            return None

        book = OrderBook(instrument.id, BookType.L1_MBP)
        ts = decision_ts
        # The book is symmetric around the fill price: the profiled price is
        # the decision, not a discovered spread.  Only the touched side has
        # size, so the simulated walk is exactly the profiled quantity.
        book.add(BookOrder(OrderSide.BUY, price, qty, 1), 0, 0, ts)
        book.add(BookOrder(OrderSide.SELL, price, qty, 2), 0, 0, ts)

        self.last_fill_price = float(price)
        self.last_filled_quantity = float(qty)
        self.last_unfilled_quantity = max(quantity - float(qty), 0.0)
        self.last_fill_source = source
        self.last_spread_cost = round(float(spread_cost), 8) if spread_cost is not None else None
        # Impact is only measurable from a book walk; a fixed profile folds its
        # cost into slippage rather than reporting a measured zero.
        self.last_impact_bps = round(float(impact), 4) if impact is not None else None

        self._fills += 1
        if source == "book":
            self._book_fills += 1
        return book

    def _walk_book(
        self,
        instrument: Any,
        order: Any,
        is_buy: bool,
        reference: float,
        quantity: float,
        tick: float,
        decision_ts: int,
    ) -> tuple[float, float, str, str | None] | None:
        provider = self.snapshot_provider
        if provider is None:
            self.last_fallback_reason = "no_book_provider"
            return None
        condition_id = self.condition_id or str(getattr(instrument, "id", ""))
        try:
            snapshot = provider.get_snapshot(condition_id, decision_ts)
        except Exception:  # noqa: BLE001 - provider failure is a fallback, not a crash
            self.last_fallback_reason = "book_snapshot_error"
            return None
        if snapshot is None:
            self.last_fallback_reason = "no_book_snapshot"
            return None

        try:
            ts_ns = int(_snapshot_field(snapshot, "ts_ns"))
        except (TypeError, ValueError):
            self.last_fallback_reason = "book_snapshot_invalid"
            return None
        if decision_ts and ts_ns > decision_ts:
            # Leakage guard: a later snapshot must never price this fill.
            self.last_fallback_reason = "book_snapshot_after_decision"
            return None
        staleness_ns = int(self.execution.book_staleness_s) * 1_000_000_000
        if decision_ts and decision_ts - ts_ns > staleness_ns:
            self.last_fallback_reason = "book_snapshot_stale"
            return None

        levels = _sorted_levels(
            _snapshot_field(snapshot, "asks") if is_buy else _snapshot_field(snapshot, "bids"),
            is_bid=not is_buy,
        )
        levels = levels[: max(int(self.execution.max_depth_levels), 1)]
        if not levels:
            self.last_fallback_reason = "book_snapshot_empty"
            return None

        remaining = quantity
        notional = 0.0
        filled = 0.0
        walked = 0
        for price, size in levels:
            take = min(size, remaining)
            if take <= 0:
                break
            notional += take * price
            filled += take
            remaining -= take
            walked += 1
            if remaining <= 1e-12:
                break

        if filled <= 0.0:
            self.last_fallback_reason = "book_snapshot_empty"
            return None

        vwap = notional / filled
        fill_px = clamp_price(vwap, tick)

        bids = _sorted_levels(_snapshot_field(snapshot, "bids") or (), is_bid=True)
        asks = _sorted_levels(_snapshot_field(snapshot, "asks") or (), is_bid=False)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        else:
            mid = reference
        if mid <= 0.0:
            self.last_fallback_reason = "book_snapshot_invalid"
            return None

        impact_bps = ((fill_px - mid) / mid * 10_000.0) if is_buy else ((mid - fill_px) / mid * 10_000.0)
        spread_cost = (fill_px - reference) if is_buy else (reference - fill_px)

        self.last_impact_bps = impact_bps
        self.last_spread_cost = spread_cost
        self.last_levels_walked = walked
        return fill_px, filled, "book", None

    def _tick_for(self, instrument: Any) -> float:
        if self.tick_size is not None:
            return float(self.tick_size)
        increment = getattr(instrument, "price_increment", None)
        if increment is not None:
            value = float(increment)
            if 0.0 < value < 0.5:
                return value
        return DEFAULT_TICK

    @staticmethod
    def _make_price(instrument: Any, value: float, tick: float) -> Price | None:
        clamped = clamp_price(value, tick)
        try:
            price = instrument.make_price(clamped)
        except Exception:  # noqa: BLE001
            try:
                price = Price.from_str(f"{clamped:.{getattr(instrument, 'price_precision', 3)}f}")
            except Exception:  # noqa: BLE001
                return None
        if not (0.0 < float(price) < 1.0):
            return None
        return price

    @staticmethod
    def _make_quantity(instrument: Any, value: float) -> Quantity | None:
        if value <= 0.0:
            return None
        try:
            qty = instrument.make_qty(value, round_down=True)
        except Exception:  # noqa: BLE001 - below the size increment
            return None
        if float(qty) <= 0.0:
            return None
        return qty


def fill_model_for(
    execution: EffectiveExecution,
    *,
    snapshot_provider: BookSnapshotProvider | None = None,
    condition_id: str | None = None,
) -> ProfiledFillModel:
    """Convenience factory used by the runner."""
    return ProfiledFillModel(
        execution,
        snapshot_provider=snapshot_provider,
        condition_id=condition_id,
    )


def with_coverage(execution: EffectiveExecution, coverage: tuple[int, int] | None) -> EffectiveExecution:
    """Record the coverage window without re-resolving the profile."""
    return replace(execution, coverage=coverage)


__all__ = [
    "BookSnapshot",
    "BookSnapshotProvider",
    "CONSERVATIVE",
    "DEFAULT_TICK",
    "EffectiveExecution",
    "NoBookProvider",
    "PROFILES",
    "PROFILE_TABLE",
    "ProfileSpec",
    "ProfiledFeeModel",
    "ProfiledFillModel",
    "calculate_cost_estimate",
    "clamp_price",
    "fill_model_for",
    "profiled_fill_price",
    "resolve_profile",
    "with_coverage",
]
