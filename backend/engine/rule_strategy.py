"""``RuleStrategy``: one Nautilus strategy class for every declarative rule set.

The strategy interprets the validated ``RunConfig`` (``backend.rules.schema``)
at runtime.  There is no code generation and no ``eval``: the tree walk only
ever compares numbers it read from the current bar, the indicator series or the
lifecycle callback.

Order intents (BRIEF §5) are explicit, because Polymarket treats YES and NO as
separate assets with separate books:

    long entry   -> BUY  primary token   (YES)
    long exit    -> SELL held token      (YES)
    short entry  -> BUY  complement      (NO)
    short exit   -> SELL held token      (NO)

A complement purchase is never treated as an exit for the primary.  One
position is held across the pair, so there is no pyramiding in v1.

Fill timing is next-bar-open: the intent is queued in ``on_bar`` (the close of
bar N) and submitted in ``on_quote`` at the open of bar N+1, where the quote
stream is the zero-spread open quote added before each bar's data
(docs/IMPLEMENTATION.md, "Nautilus 2.0 notes").  Orders are always market
orders; the execution profile decides the fill price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from nautilus_trader.model import (
    Bar,
    InstrumentId,
    OrderSide,
    Quantity,
    QuoteTick,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from backend.rules.schema import (
    LIFECYCLE_OPERANDS,
    PRICE_OPERANDS,
    RunConfig,
    SizingConfig,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only to avoid an import cycle
    from backend.engine.signals import SignalPlan

ACTIONS = ("long_entry", "long_exit", "short_entry", "short_exit")
_COMPARATORS = ("gt", "gte", "lt", "lte")
_CROSSES = ("crosses_above", "crosses_below")


# ---------------------------------------------------------------------------
# Operands and the rule tree walk


class OperandError(ValueError):
    """Raised when a validated tree contains an operand the strategy cannot read."""


class OperandResolver:
    """Resolve one operand against the current bar, indicators and lifecycle."""

    __slots__ = ("bar", "indicators", "lifecycle")

    def __init__(
        self,
        bar: Mapping[str, Any] | None = None,
        indicators: Mapping[str, Mapping[str, Any]] | None = None,
        lifecycle: Mapping[str, Any] | None = None,
    ) -> None:
        self.bar = bar or {}
        self.indicators = indicators or {}
        self.lifecycle = lifecycle or {}

    def value(self, operand: Any) -> Any:
        if isinstance(operand, bool):
            return 1.0 if operand else 0.0
        if isinstance(operand, (int, float)):
            return float(operand)
        if not isinstance(operand, str):
            raise OperandError(f"unsupported operand {operand!r}")
        if operand in PRICE_OPERANDS:
            return self.bar.get(operand)
        if operand in LIFECYCLE_OPERANDS or operand == "phase":
            return self.lifecycle.get(operand)
        if operand.startswith("indicator:"):
            parts = operand.split(":")
            if len(parts) != 3:
                raise OperandError(f"malformed indicator reference {operand!r}")
            instance = self.indicators.get(parts[1]) or {}
            if not isinstance(instance, Mapping):
                instance = {"value": instance}
            return instance.get(parts[2])
        raise OperandError(f"unknown operand {operand!r}")


def _single(node: Any) -> tuple[str, Any]:
    if not isinstance(node, Mapping) or len(node) != 1:
        raise ValueError("a condition must be an object with exactly one key")
    return next(iter(node.items()))


def evaluate_condition(
    tree: Mapping[str, Any],
    resolve: OperandResolver | Callable[[Any], Any],
    previous: Mapping[str, Any] | None = None,
) -> bool:
    """Evaluate a validated declarative condition.

    ``resolve`` is an :class:`OperandResolver` or a callable.  ``previous`` maps
    operand -> value from the prior bar for ``crosses_*``; a missing value makes
    the cross false (never a silent zero).

    Internally the tree is evaluated with three-valued logic: an operand that is
    ``None`` (indicator warm-up, unknown lifecycle phase) makes its node
    ``None`` rather than ``False``, so ``not`` cannot turn "not ready" into a
    signal.  Only the top-level result is coerced to ``bool``.
    """
    get = resolve.value if isinstance(resolve, OperandResolver) else resolve
    prev = previous or {}

    def walk(node: Any) -> bool | None:
        key, value = _single(node)
        if key == "all":
            children = [walk(child) for child in value]
            if any(child is False for child in children):
                return False
            if any(child is None for child in children):
                return None
            return True
        if key == "any":
            children = [walk(child) for child in value]
            if any(child is True for child in children):
                return True
            if any(child is None for child in children):
                return None
            return False
        if key == "not":
            child = walk(value)
            if child is None:
                return None
            return not child
        if key in _COMPARATORS:
            left = _as_number(get(value[0]))
            right = _as_number(get(value[1]))
            if left is None or right is None:
                return None
            if key == "gt":
                return left > right
            if key == "gte":
                return left >= right
            if key == "lt":
                return left < right
            return left <= right
        if key in _CROSSES:
            left_operand, right_operand = value
            left = _as_number(get(left_operand))
            right = _as_number(get(right_operand))
            prev_left = _as_number(prev.get(left_operand))
            prev_right = _as_number(prev.get(right_operand))
            if None in (left, right, prev_left, prev_right):
                return None
            if key == "crosses_above":
                return prev_left <= prev_right and left > right
            return prev_left >= prev_right and left < right
        raise ValueError(f"unknown condition key {key!r}")

    return bool(walk(tree))


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None if value is None else (1.0 if value else 0.0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def collect_cross_operands(tree: Mapping[str, Any] | None, out: set[str]) -> None:
    """Collect every string operand used by a ``crosses_*`` node."""
    if tree is None:
        return
    key, value = _single(tree)
    if key in ("all", "any"):
        for child in value:
            collect_cross_operands(child, out)
        return
    if key == "not":
        collect_cross_operands(value, out)
        return
    if key in _CROSSES:
        for operand in value:
            if isinstance(operand, str):
                out.add(operand)
        return
    # Other comparators need the current value only, not the previous one.


# ---------------------------------------------------------------------------
# Sizing


def stake_for(sizing: SizingConfig, reference_price: float) -> float:
    """Stake in USD for one entry at ``reference_price``."""
    if not (0.0 < float(reference_price) < 1.0):
        raise ValueError(f"reference_price must be in (0, 1), got {reference_price!r}")
    if sizing.mode == "fixed_stake":
        return float(sizing.value)
    if sizing.mode == "risk_pct":
        return float(sizing.bankroll_usd) * float(sizing.value) / 100.0
    raise ValueError(f"unknown sizing mode {sizing.mode!r}")


def compute_sizing(
    sizing: SizingConfig, reference_price: float, instrument: Any
) -> Quantity | None:
    """Contracts for one entry: stake / price, floored to the size increment.

    The result is at least one size increment (0.01) when a stake is positive;
    ``None`` only when the instrument cannot represent a positive quantity.
    """
    stake = stake_for(sizing, reference_price)
    increment = float(instrument.size_increment)
    if increment <= 0.0:
        return None
    raw = stake / float(reference_price)
    steps = math.floor(raw / increment + 1e-9)
    contracts = max(steps, 1) * increment
    contracts = round(contracts, int(instrument.size_precision))
    try:
        quantity = instrument.make_qty(contracts, round_down=True)
    except Exception:  # noqa: BLE001 - below the venue increment / bad precision
        return None
    return quantity if float(quantity) > 0.0 else None


def position_sizing(
    sizing: SizingConfig, reference_price: float, instrument: Any
) -> dict[str, Any]:
    """Pre-trade size and exposure summary (no fees, no profitability claim)."""
    quantity = compute_sizing(sizing, reference_price, instrument)
    if quantity is None:
        return {
            "stake_usd": stake_for(sizing, reference_price),
            "contracts": None,
            "notional_usd": None,
            "max_loss_usd": None,
            "max_payout_usd": None,
            "max_profit_usd": None,
        }
    contracts = float(quantity)
    notional = contracts * float(reference_price)
    payout = contracts * 1.0
    return {
        "stake_usd": stake_for(sizing, reference_price),
        "contracts": contracts,
        "notional_usd": round(notional, 6),
        "max_loss_usd": round(notional, 6),
        "max_payout_usd": round(payout, 6),
        "max_profit_usd": round(payout - notional, 6),
    }


# ---------------------------------------------------------------------------
# Intents and positions


@dataclass(frozen=True)
class OrderIntent:
    """One queued market order, before sizing at the next bar's open."""

    action: str
    instrument_id: str
    token: str
    side: str  # buy | sell
    ready_ts_ns: int = 0  # close of the signal bar = open of the next bar


@dataclass
class PositionState:
    token: str
    instrument_id: str
    direction: str  # long (primary) | short (complement)
    quantity: float
    entry_price: float
    entry_ts_ns: int
    entry_fee_usd: float = 0.0
    realised_fee_usd: float = 0.0
    exit_notional: float = 0.0
    exit_quantity: float = 0.0
    entry_quantity: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "entry_ts_ns": self.entry_ts_ns,
            "fees_usd": round(self.entry_fee_usd + self.realised_fee_usd, 6),
        }


@dataclass
class RuleStrategyConfig:
    """Everything the strategy needs, independent of the Nautilus kernel."""

    run_config: RunConfig
    primary: Any
    complement: Any
    indicator_specs: Sequence[Any] | None = None
    lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None = None
    signal_sink: list[dict[str, Any]] | None = None
    fill_sink: list[dict[str, Any]] | None = None
    fill_metadata: Callable[[], Mapping[str, Any]] | None = None
    indicator_set_factory: Callable[[Sequence[Any]], Any] | None = None
    #: Precomputed execution-independent truths (backend.engine.signals); when
    #: absent the strategy evaluates the rule trees itself.
    signal_plan: "SignalPlan | None" = None


def _default_indicator_factory(instances: Sequence[Any]) -> Any:
    # Imported lazily: the indicators package is the single implementation for
    # chart and backtest, and this module must import without it during tests.
    from backend.indicators.compute import IndicatorSet  # noqa: PLC0415

    return IndicatorSet(instances)


_NO_INDICATORS = object()


class _EmptyIndicators:
    def update(self, _bar: Mapping[str, Any]) -> "_EmptyIndicators":
        return self

    def values(self) -> dict[str, dict[str, Any]]:
        return {}


def normalise_indicator_values(result: Any) -> dict[str, dict[str, Any]]:
    """``instance_id -> {output: value}`` from an indicator set or its result.

    Shared with :mod:`backend.engine.signals` so a plan snapshot and a runtime
    snapshot have exactly the same shape.
    """
    values = getattr(result, "values", None)
    if callable(values):
        raw = values()
    elif isinstance(result, Mapping):
        raw = result
    else:
        raw = {}
    out: dict[str, dict[str, Any]] = {}
    for instance_id, instance_values in (raw or {}).items():
        if isinstance(instance_values, Mapping):
            out[str(instance_id)] = dict(instance_values)
        else:
            out[str(instance_id)] = {"value": instance_values}
    return out


class RuleStrategy(Strategy):
    """Interpret a validated ``RunConfig`` over one token pair."""

    def __new__(cls, *_args: Any, **_kwargs: Any) -> "RuleStrategy":
        # ``Strategy.__new__`` is a Cython slot that only accepts the strategy
        # config; ignore the rule arguments here and build the Nautilus config
        # in ``__init__`` where it is actually used.
        return super().__new__(cls, StrategyConfig())

    def __init__(
        self,
        config: RuleStrategyConfig | RunConfig,
        primary: Any | None = None,
        complement: Any | None = None,
        indicator_specs: Sequence[Any] | None = None,
        lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None = None,
        signal_sink: list[dict[str, Any]] | None = None,
        fill_sink: list[dict[str, Any]] | None = None,
        fill_metadata: Callable[[], Mapping[str, Any]] | None = None,
        *,
        indicator_set_factory: Callable[[Sequence[Any]], Any] | None = None,
        nautilus_config: StrategyConfig | None = None,
        signal_plan: "SignalPlan | None" = None,
        subscribe: bool = True,
    ) -> None:
        super().__init__(nautilus_config or StrategyConfig())

        if isinstance(config, RuleStrategyConfig):
            rule_config = config
        else:
            rule_config = RuleStrategyConfig(
                run_config=config,
                primary=primary,
                complement=complement,
                indicator_specs=indicator_specs,
                lifecycle_lookup=lifecycle_lookup,
                signal_sink=signal_sink,
                fill_sink=fill_sink,
                fill_metadata=fill_metadata,
                indicator_set_factory=indicator_set_factory,
                signal_plan=signal_plan,
            )

        self._subscribe = bool(subscribe)
        if rule_config.run_config is None:
            raise ValueError("RuleStrategy requires a validated RunConfig")
        if rule_config.primary is None or rule_config.complement is None:
            raise ValueError("RuleStrategy requires the primary and complement instruments")

        self.run_config = rule_config.run_config
        self.strategy_config = self.run_config.strategy
        self.primary = rule_config.primary
        self.complement = rule_config.complement
        self.primary_id = str(self.primary.id)
        self.complement_id = str(self.complement.id)
        self._primary_token = str(getattr(self.primary, "outcome", "YES") or "YES").upper()
        self._complement_token = str(
            getattr(self.complement, "outcome", "NO") or "NO"
        ).upper()

        self.indicator_specs = list(
            rule_config.indicator_specs
            if rule_config.indicator_specs is not None
            else self.strategy_config.indicators
        )
        self.lifecycle_lookup = rule_config.lifecycle_lookup
        self.signal_sink = rule_config.signal_sink if rule_config.signal_sink is not None else []
        self.fill_sink = rule_config.fill_sink if rule_config.fill_sink is not None else []
        self.fill_metadata = rule_config.fill_metadata
        self._indicator_factory = (
            rule_config.indicator_set_factory or _default_indicator_factory
        )
        self._indicators: Any = _NO_INDICATORS
        self.signal_plan = rule_config.signal_plan
        if self.signal_plan is not None:
            # A plan computed for another strategy would silently change the
            # decisions this strategy takes; refuse it instead.
            from backend.rules.schema import strategy_hash as _strategy_hash  # noqa: PLC0415

            expected = _strategy_hash(self.run_config)
            if self.signal_plan.strategy_hash != expected:
                raise ValueError(
                    "signal plan is for a different strategy: "
                    f"{self.signal_plan.strategy_hash} != {expected}"
                )

        self._trees = {
            "long_entry": self.strategy_config.long_entry,
            "long_exit": self.strategy_config.long_exit,
            "short_entry": self.strategy_config.short_entry,
            "short_exit": self.strategy_config.short_exit,
        }
        self._cross_operands: set[str] = set()
        for tree in self._trees.values():
            collect_cross_operands(tree, self._cross_operands)
        self._previous_operands: dict[str, Any] = {}

        self._pending: OrderIntent | None = None
        self._position: PositionState | None = None
        self.trades: list[dict[str, Any]] = []
        self._diagnostics = {
            "bars": 0,
            "signals_generated": 0,
            "orders_submitted": 0,
            "fills": 0,
            "rejects": 0,
            "trades_completed": 0,
            "early_exits": 0,
            "ambiguous_entries": 0,
            "insufficient_stake": 0,
            "missing_quote": 0,
            "wrong_instrument_quote": 0,
            "unexpected_fills": 0,
            "unfilled_remainders": 0,
            "late_intents": 0,
            "indicator_not_ready_bars": 0,
            "lifecycle_blocked_bars": 0,
            "missing_lifecycle_bars": 0,
        }
        self._first_bar_ts_ns: int | None = None
        self._last_bar_ts_ns: int | None = None
        self._in_market_ns = 0
        self._entry_ts_ns: int | None = None
        self._stake_usd: float | None = None
        self._last_reference: float | None = None
        self._reject_reasons: list[str] = []
        self._last_signal_ts_ns: int | None = None
        self._signal_gaps_ns: list[int] = []
        self._holding_periods_ns: list[int] = []
        self._stopped = False
        self.diagnostics_snapshot: dict[str, Any] = {}

    @classmethod
    def create(
        cls,
        run_config: RunConfig,
        primary: Any,
        complement: Any,
        indicator_specs: Sequence[Any] | None = None,
        lifecycle_lookup: Callable[[int], Mapping[str, Any] | None] | None = None,
        signal_sink: list[dict[str, Any]] | None = None,
        *,
        fill_sink: list[dict[str, Any]] | None = None,
        indicator_set_factory: Callable[[Sequence[Any]], Any] | None = None,
        nautilus_config: StrategyConfig | None = None,
        signal_plan: "SignalPlan | None" = None,
        subscribe: bool = True,
    ) -> "RuleStrategy":
        """Positional-argument constructor for the runner."""
        return cls(
            run_config,
            primary=primary,
            complement=complement,
            indicator_specs=indicator_specs,
            lifecycle_lookup=lifecycle_lookup,
            signal_sink=signal_sink,
            fill_sink=fill_sink,
            indicator_set_factory=indicator_set_factory,
            nautilus_config=nautilus_config,
            signal_plan=signal_plan,
            subscribe=subscribe,
        )

    # -- read-only views --------------------------------------------------

    @property
    def signals(self) -> list[dict[str, Any]]:
        return self.signal_sink

    @property
    def position(self) -> PositionState | None:
        return self._position

    @property
    def pending_intent(self) -> OrderIntent | None:
        return self._pending

    def open_positions(self) -> list[dict[str, Any]]:
        return [self._position.to_json()] if self._position is not None else []

    def diagnostics(self) -> dict[str, Any]:
        out = dict(self._diagnostics)
        out["average_time_between_signals_s"] = (
            (sum(self._signal_gaps_ns) / len(self._signal_gaps_ns)) / 1_000_000_000
            if self._signal_gaps_ns
            else None
        )
        out["average_holding_time_s"] = (
            (sum(self._holding_periods_ns) / len(self._holding_periods_ns)) / 1_000_000_000
            if self._holding_periods_ns
            else None
        )
        span = (
            (self._last_bar_ts_ns - self._first_bar_ts_ns)
            if self._first_bar_ts_ns is not None and self._last_bar_ts_ns is not None
            else 0
        )
        in_market = self._in_market_ns
        if self._position is not None and self._entry_ts_ns is not None and self._last_bar_ts_ns is not None:
            in_market += max(self._last_bar_ts_ns - self._entry_ts_ns, 0)
        out["time_in_market_pct"] = round(100.0 * in_market / span, 4) if span > 0 else 0.0
        out["open_positions"] = len(self.open_positions())
        out["reject_reasons"] = list(self._reject_reasons)
        return out

    # -- Nautilus lifecycle ----------------------------------------------

    def on_start(self) -> None:
        if not self._subscribe:
            return
        # A runner may subscribe instead; this keeps a directly-added strategy
        # working and is idempotent for the backtest engine.
        try:
            bar_type = self.strategy_config.bar_type
            if bar_type:
                self.subscribe_bars(_bar_type(bar_type))
            self.subscribe_quotes(
                [InstrumentId.from_str(self.primary_id), InstrumentId.from_str(self.complement_id)]
            )
        except Exception:  # noqa: BLE001 - never abort a run for subscriptions
            pass

    def on_stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.diagnostics_snapshot = self.diagnostics()
        try:
            self.log.info(f"RuleStrategy diagnostics: {self.diagnostics_snapshot}")
        except Exception:  # noqa: BLE001 - logging must never fail a stop
            pass

    # -- market data ------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        bar_dict = bar_to_dict(bar)
        ts_ns = int(bar_dict["ts_event"])
        self._diagnostics["bars"] += 1
        if self._first_bar_ts_ns is None:
            self._first_bar_ts_ns = ts_ns
        if self._last_bar_ts_ns is not None and ts_ns > self._last_bar_ts_ns:
            if self._position is not None:
                self._in_market_ns += ts_ns - self._last_bar_ts_ns
        self._last_bar_ts_ns = ts_ns

        plan = self.signal_plan
        if plan is not None:
            # Execution-independent path: consume the precomputed truths and
            # the indicator snapshot instead of recomputing either.
            row = plan.row_at(ts_ns)
            if row is None:
                raise ValueError(
                    f"bar {ts_ns} is not in the signal plan for "
                    f"{plan.bar_type} ({len(plan)} rows)"
                )
            indicator_values = {
                instance_id: dict(values)
                for instance_id, values in row.indicator_values.items()
            }
            lifecycle = dict(row.lifecycle)
            if not lifecycle:
                self._diagnostics["missing_lifecycle_bars"] += 1
            truth = row.truth
            bar_close = row.close
        else:
            indicator_values = self._update_indicators(bar_dict)
            lifecycle = self._lifecycle(ts_ns)
            resolver = OperandResolver(bar_dict, indicator_values, lifecycle)
            current_cross_values = {
                operand: resolver.value(operand) for operand in self._cross_operands
            }

            def truth(action: str, *, _resolver: OperandResolver = resolver) -> bool:
                tree = self._trees[action]
                return tree is not None and evaluate_condition(
                    tree, _resolver, self._previous_operands
                )

            bar_close = _as_number(bar_dict.get("close"))

        if indicator_values and self._any_missing(indicator_values):
            self._diagnostics["indicator_not_ready_bars"] += 1

        intent = self._decide(
            ts_ns=ts_ns,
            ready_ts_ns=int(bar_dict.get("ts_init") or ts_ns),
            lifecycle=lifecycle,
            indicator_values=indicator_values,
            bar_close=bar_close,
            truth=truth,
        )

        if plan is None:
            self._previous_operands = current_cross_values
        self._pending = intent

    def on_quote(self, quote: QuoteTick) -> None:
        intent = self._pending
        if intent is None:
            return
        instrument_id = str(quote.instrument_id)
        if instrument_id != intent.instrument_id:
            self._diagnostics["wrong_instrument_quote"] += 1
            return
        ts_event = int(quote.ts_event)
        if intent.ready_ts_ns and ts_event < intent.ready_ts_ns:
            # Still inside the signal bar: the next bar has not opened yet.
            return
        # The next bar's open may be later than the signal bar's close when the
        # A gap between the signal bar's close and the next open is still the
        # next bar; it is not a late fill. Only a quote strictly before the
        # close would indicate a replay-order error.

        self._pending = None
        if intent.side == "buy":
            reference = float(quote.ask_price)
        else:
            reference = float(quote.bid_price)
        if not (0.0 < reference < 1.0):
            self._diagnostics["missing_quote"] += 1
            return

        instrument = self.primary if intent.instrument_id == self.primary_id else self.complement
        if intent.side == "sell" and self._position is not None:
            # Closing sells exactly the held token quantity; sizing a new stake
            # here would oversell (or undersell) the position.
            quantity = instrument.make_qty(self._position.quantity)
        else:
            quantity = compute_sizing(self.run_config.risk.sizing, reference, instrument)
        if quantity is None:
            self._diagnostics["insufficient_stake"] += 1
            return
        self._stake_usd = float(quantity) * reference
        self._last_reference = reference
        self._submit_market(instrument, intent.side, quantity, int(quote.ts_event))

    # -- order events -----------------------------------------------------

    def on_order_filled(self, event: Any) -> None:
        try:
            self._apply_fill(event)
        except Exception as exc:  # noqa: BLE001 - a booking bug must not kill the run
            self._diagnostics["unexpected_fills"] += 1
            try:
                self.log.error(f"failed to book fill: {exc}")
            except Exception:  # noqa: BLE001
                pass
        self._cancel_remainder(event)

    def _cancel_remainder(self, event: Any) -> None:
        """Book partial depth as unfilled instead of leaving a live remainder."""
        client_order_id = getattr(event, "client_order_id", None)
        if client_order_id is None:
            return
        try:
            order = self.cache.order(client_order_id)
        except Exception:  # noqa: BLE001 - no cache outside a kernel
            return
        if order is None:
            return
        try:
            if bool(order.is_open) and float(order.leaves_qty) > 0.0:
                self._diagnostics["unfilled_remainders"] += 1
                self.cancel_order(client_order_id)
        except Exception:  # noqa: BLE001
            pass

    def on_order_rejected(self, event: Any) -> None:
        self._diagnostics["rejects"] += 1
        self._pending = None
        try:
            self._reject_reasons.append(str(getattr(event, "reason", "unknown")))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.log.warning(
                f"order rejected: {getattr(event, 'reason', 'unknown')} "
                f"({getattr(event, 'client_order_id', '?')})"
            )
        except Exception:  # noqa: BLE001
            pass

    # -- decision ---------------------------------------------------------

    def _decide(
        self,
        *,
        ts_ns: int,
        ready_ts_ns: int,
        lifecycle: Mapping[str, Any],
        indicator_values: Mapping[str, Mapping[str, Any]],
        bar_close: float | None,
        truth: Callable[[str], bool],
    ) -> OrderIntent | None:
        direction = self.strategy_config.direction
        settlement = self.strategy_config.settlement

        if self._position is not None:
            if settlement == "hold_to_expiry":
                return None
            action = "long_exit" if self._position.direction == "long" else "short_exit"
            tree = self._trees[action]
            if tree is None:
                return None
            if truth(action):
                intent = self._intent_for(action, ready_ts_ns=ready_ts_ns)
                self._record_signal(ts_ns, action, bar_close, indicator_values)
                return intent
            return None

        allows = self._lifecycle_allows(lifecycle)
        if not allows:
            self._diagnostics["lifecycle_blocked_bars"] += 1
            return None

        max_trades = getattr(self.run_config.risk, "max_trades", None)
        if max_trades is not None and len(self.trades) >= int(max_trades):
            return None

        long_tree = self._trees["long_entry"]
        short_tree = self._trees["short_entry"]
        long_ok = (
            direction in ("long", "both")
            and long_tree is not None
            and truth("long_entry")
        )
        short_ok = (
            direction in ("short", "both")
            and short_tree is not None
            and truth("short_entry")
        )

        if long_ok and short_ok:
            self._diagnostics["ambiguous_entries"] += 1
            short_ok = direction == "short"

        action: str | None = None
        if long_ok:
            action = "long_entry"
        elif short_ok:
            action = "short_entry"
        if action is None:
            return None
        self._record_signal(ts_ns, action, bar_close, indicator_values)
        return self._intent_for(action, ready_ts_ns=ready_ts_ns)

    def _intent_for(self, action: str, ready_ts_ns: int = 0) -> OrderIntent:
        if action in ("long_entry", "long_exit"):
            token, instrument_id = self._primary_token, self.primary_id
        else:
            token, instrument_id = self._complement_token, self.complement_id
        side = "buy" if action in ("long_entry", "short_entry") else "sell"
        return OrderIntent(
            action=action,
            instrument_id=instrument_id,
            token=token,
            side=side,
            ready_ts_ns=ready_ts_ns,
        )

    def _record_signal(
        self,
        ts_ns: int,
        action: str,
        bar_close: float | None,
        indicator_values: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._diagnostics["signals_generated"] += 1
        if self._last_signal_ts_ns is not None and ts_ns >= self._last_signal_ts_ns:
            self._signal_gaps_ns.append(ts_ns - self._last_signal_ts_ns)
        self._last_signal_ts_ns = ts_ns
        self.signal_sink.append(
            {
                "ts_ns": ts_ns,
                "action": action,
                "ref_price": bar_close,
                "indicator_values": {
                    instance_id: dict(values)
                    for instance_id, values in (indicator_values or {}).items()
                },
            }
        )

    # -- submission -------------------------------------------------------

    def _submit_market(self, instrument: Any, side: str, quantity: Quantity, ts_ns: int) -> None:
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        # A sell always closes a held token; without reduce_only the CASH
        # account would treat it as a naked short and reject it.
        order = self.order_factory.market(
            instrument.id, order_side, quantity, reduce_only=(side == "sell")
        )
        self.submit_order(order)
        self._diagnostics["orders_submitted"] += 1

    # -- booking ----------------------------------------------------------

    def _apply_fill(self, event: Any) -> None:
        instrument_id = str(event.instrument_id)
        if instrument_id not in (self.primary_id, self.complement_id):
            self._diagnostics["unexpected_fills"] += 1
            return
        is_buy = bool(event.is_buy)
        quantity = float(event.last_qty)
        price = float(event.last_px)
        commission = getattr(event, "commission", None)
        fee = float(commission) if commission is not None else 0.0
        ts_ns = int(event.ts_event)
        self._diagnostics["fills"] += 1

        token = self._primary_token if instrument_id == self.primary_id else self._complement_token
        try:
            fill_meta = dict(self.fill_metadata()) if self.fill_metadata is not None else {}
        except Exception:  # noqa: BLE001
            fill_meta = {}
        self.fill_sink.append(
            {
                "ts_ns": ts_ns,
                "execution_source": fill_meta.get("execution_source"),
                "fallback_reason": fill_meta.get("fallback_reason"),
                "impact_bps": fill_meta.get("impact_bps"),
                "instrument_id": instrument_id,
                "token": token,
                "side": "BUY" if is_buy else "SELL",
                "quantity": quantity,
                "price": price,
                "fee_usd": fee,
                "reference_price": self._last_reference,
            }
        )

        if is_buy:
            direction = "long" if instrument_id == self.primary_id else "short"
            if self._position is None:
                self._position = PositionState(
                    token=token,
                    instrument_id=instrument_id,
                    direction=direction,
                    quantity=quantity,
                    entry_price=price,
                    entry_ts_ns=ts_ns,
                    entry_fee_usd=fee,
                    entry_quantity=quantity,
                )
                self._entry_ts_ns = ts_ns
            elif self._position.token == token:
                # No pyramiding: a second entry fill would be an engine bug.
                self._diagnostics["unexpected_fills"] += 1
                position = self._position
                total = position.quantity + quantity
                position.entry_price = (
                    position.entry_price * position.quantity + price * quantity
                ) / total
                position.quantity = total
                position.entry_quantity += quantity
                position.entry_fee_usd += fee
            else:
                self._diagnostics["unexpected_fills"] += 1
            return

        position = self._position
        if position is None or position.token != token:
            self._diagnostics["unexpected_fills"] += 1
            return
        position.realised_fee_usd += fee
        position.exit_notional += quantity * price
        position.exit_quantity += quantity
        remaining = position.quantity - quantity
        if remaining > 1e-9:
            position.quantity = remaining
            return

        exit_price = (
            position.exit_notional / position.exit_quantity
            if position.exit_quantity > 0
            else price
        )
        self._close_position(position, exit_price, ts_ns)

    def _close_position(self, position: PositionState, exit_price: float, ts_ns: int) -> None:
        pnl = (exit_price - position.entry_price) * position.entry_quantity
        fees = position.entry_fee_usd + position.realised_fee_usd
        record = {
            "token": position.token,
            "direction": position.direction,
            "instrument_id": position.instrument_id,
            "quantity": position.entry_quantity,
            "entry_ts_ns": position.entry_ts_ns,
            "exit_ts_ns": ts_ns,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "fees_usd": round(fees, 6),
            "pnl_usd": round(pnl - fees, 6),
        }
        self.trades.append(record)
        self._holding_periods_ns.append(max(ts_ns - position.entry_ts_ns, 0))
        self._diagnostics["trades_completed"] += 1
        self._diagnostics["early_exits"] += 1
        self._position = None
        self._entry_ts_ns = None

    # -- helpers ----------------------------------------------------------

    def _update_indicators(self, bar_dict: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        if self._indicators is _NO_INDICATORS:
            if not self.indicator_specs:
                self._indicators = _EmptyIndicators()
            else:
                specs = [
                    spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
                    for spec in self.indicator_specs
                ]
                self._indicators = self._indicator_factory(specs)
        result = self._indicators.update(dict(bar_dict))
        if result is None:
            result = self._indicators
        return normalise_indicator_values(result)

    def _any_missing(self, values: Mapping[str, Mapping[str, Any]]) -> bool:
        return any(
            value is None
            for instance in values.values()
            for value in instance.values()
        )

    def _lifecycle(self, ts_ns: int) -> dict[str, Any]:
        if self.lifecycle_lookup is None:
            self._diagnostics["missing_lifecycle_bars"] += 1
            return {}
        try:
            values = self.lifecycle_lookup(ts_ns)
        except Exception:  # noqa: BLE001 - lifecycle is advisory, never fatal
            values = None
        if not values:
            self._diagnostics["missing_lifecycle_bars"] += 1
            return {}
        return dict(values)

    def _lifecycle_allows(self, lifecycle: Mapping[str, Any]) -> bool:
        filters = self.strategy_config.lifecycle_filters
        if filters is None:
            return True
        phase = lifecycle.get("phase")
        if filters.require_known_phase and phase in (None, "", "unknown"):
            return False
        if filters.phases is not None and phase not in filters.phases:
            return False
        ttk = _as_number(lifecycle.get("time_to_kickoff_minutes"))
        if filters.time_to_kickoff_min_minutes is not None:
            if ttk is None or ttk < filters.time_to_kickoff_min_minutes:
                return False
        if filters.time_to_kickoff_max_minutes is not None:
            if ttk is None or ttk > filters.time_to_kickoff_max_minutes:
                return False
        tts = _as_number(lifecycle.get("time_to_settlement_minutes"))
        if filters.time_to_settlement_min_minutes is not None:
            if tts is None or tts < filters.time_to_settlement_min_minutes:
                return False
        return True


def _bar_type(value: str) -> Any:
    from nautilus_trader.model import BarType  # noqa: PLC0415 - avoids a cycle at import

    return BarType.from_str(value)


def bar_to_dict(bar: Any, trade_count: int | None = None) -> dict[str, Any]:
    """Canonical bar mapping used by the indicator set and the rule operands.

    Nautilus bars carry no ``trade_count``; callers that know it (the bar
    builders) pass it explicitly so the canonical field is never invented.
    """
    return {
        "instrument_id": str(bar.bar_type.instrument_id),
        "bar_type": str(bar.bar_type),
        "ts_event": int(bar.ts_event),
        "ts_init": int(bar.ts_init),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
        "trade_count": (
            trade_count if trade_count is not None else getattr(bar, "trade_count", None)
        ),
    }


__all__ = [
    "OrderIntent",
    "PositionState",
    "RuleStrategy",
    "RuleStrategyConfig",
    "OperandResolver",
    "bar_to_dict",
    "collect_cross_operands",
    "compute_sizing",
    "evaluate_condition",
    "normalise_indicator_values",
    "position_sizing",
    "stake_for",
]
