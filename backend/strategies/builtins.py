"""The built-in strategy catalogue (BRIEF §8.1).

Fifteen curated, versioned strategies.  Each builder returns a
``StrategySection``-compatible dict built only from registry indicators and
declarative rules; nothing here generates code, calls ``eval`` or draws a
random number at runtime.

``catalogue()`` is the API surface: metadata, tunable parameters, defaults and
the full rule set, with every entry validated through ``RunConfig`` before it is
returned.  ``random_entry`` is explicitly a null baseline, not a hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from backend.indicators.registry import validate_params
from backend.rules.schema import (
    DateRange,
    EvaluationConfig,
    ExecutionConfig,
    RiskConfig,
    RunConfig,
    StrategySection,
)

CATALOGUE_VERSION = "1"
REFERENCE_INSTRUMENT_ID = "CATALOGUE-REFERENCE-YES.POLYMARKET"
REFERENCE_BAR_TYPE = "1m"


# ---------------------------------------------------------------------------
# Parameter metadata


@dataclass(frozen=True)
class ParamDef:
    key: str
    label: str
    type: str  # int | float
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
        }


def _params(
    defs: Sequence[ParamDef], supplied: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Merge defaults with user params; unknown keys and ranges are rejected."""
    supplied = dict(supplied or {})
    known = {d.key for d in defs}
    unknown = set(supplied) - known
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for definition in defs:
        value = supplied.get(definition.key, definition.default)
        if definition.type == "int":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{definition.key} must be an integer")
            if int(value) != float(value):
                raise ValueError(f"{definition.key} must be an integer")
            value = int(value)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{definition.key} must be a number")
            value = float(value)
        if definition.min is not None and value < definition.min:
            raise ValueError(f"{definition.key} must be >= {definition.min:g}")
        if definition.max is not None and value > definition.max:
            raise ValueError(f"{definition.key} must be <= {definition.max:g}")
        out[definition.key] = value
    return out


def _indicator(
    instance_id: str, indicator_type: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    validated = validate_params(indicator_type, dict(params or {}))
    return {"instance_id": instance_id, "type": indicator_type, "params": validated}


def _section(
    instrument_id: str,
    bar_type: str,
    *,
    indicators: Sequence[dict[str, Any]],
    long_entry: Mapping[str, Any] | None,
    long_exit: Mapping[str, Any] | None = None,
    short_entry: Mapping[str, Any] | None = None,
    short_exit: Mapping[str, Any] | None = None,
    direction: str = "both",
    settlement: str = "rule_exit",
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "indicators": list(indicators),
        "long_entry": dict(long_entry) if long_entry is not None else None,
        "long_exit": dict(long_exit) if long_exit is not None else None,
        "short_entry": dict(short_entry) if short_entry is not None else None,
        "short_exit": dict(short_exit) if short_exit is not None else None,
        "direction": direction,
        "settlement": settlement,
    }


# ---------------------------------------------------------------------------
# Builders


EMACROSS_PARAMS = (
    ParamDef("fast_period", "Fast EMA period", "int", 12, 2, 200, 1),
    ParamDef("slow_period", "Slow EMA period", "int", 26, 3, 400, 1),
)


def nautilus_ema_cross(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fast EMA crossing a slow EMA, both directions."""
    p = _params(EMACROSS_PARAMS, params)
    if p["fast_period"] >= p["slow_period"]:
        raise ValueError("fast_period must be smaller than slow_period")
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("ema_fast", "ema", {"period": p["fast_period"]}),
            _indicator("ema_slow", "ema", {"period": p["slow_period"]}),
        ],
        long_entry={
            "crosses_above": ["indicator:ema_fast:value", "indicator:ema_slow:value"]
        },
        long_exit={
            "crosses_below": ["indicator:ema_fast:value", "indicator:ema_slow:value"]
        },
        short_entry={
            "crosses_below": ["indicator:ema_fast:value", "indicator:ema_slow:value"]
        },
        short_exit={
            "crosses_above": ["indicator:ema_fast:value", "indicator:ema_slow:value"]
        },
    )


SMA_TREND_PARAMS = (
    ParamDef("trend_period", "Trend SMA period", "int", 50, 2, 400, 1),
    ParamDef("filter_period", "Filter SMA period", "int", 200, 3, 500, 1),
)


def sma_trend(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Close above a trend SMA, with the trend above a slower filter SMA."""
    p = _params(SMA_TREND_PARAMS, params)
    if p["trend_period"] >= p["filter_period"]:
        raise ValueError("trend_period must be smaller than filter_period")
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("sma_trend", "sma", {"period": p["trend_period"]}),
            _indicator("sma_filter", "sma", {"period": p["filter_period"]}),
        ],
        long_entry={
            "all": [
                {"gt": ["close", "indicator:sma_trend:value"]},
                {"gt": ["indicator:sma_trend:value", "indicator:sma_filter:value"]},
            ]
        },
        long_exit={"lt": ["close", "indicator:sma_trend:value"]},
        short_entry={
            "all": [
                {"lt": ["close", "indicator:sma_trend:value"]},
                {"lt": ["indicator:sma_trend:value", "indicator:sma_filter:value"]},
            ]
        },
        short_exit={"gt": ["close", "indicator:sma_trend:value"]},
    )


RSI_PARAMS = (
    ParamDef("period", "RSI period", "int", 14, 2, 200, 1),
    ParamDef("oversold", "Oversold level", "float", 30.0, 1.0, 49.0, 1.0),
    ParamDef("mid_level", "Mid level", "float", 50.0, 2.0, 98.0, 1.0),
    ParamDef("overbought", "Overbought level", "float", 70.0, 51.0, 99.0, 1.0),
)


def rsi_reversion(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Buy the recovery off oversold, fade the fade off overbought."""
    p = _params(RSI_PARAMS, params)
    if not (p["oversold"] < p["mid_level"] < p["overbought"]):
        raise ValueError("oversold < mid_level < overbought is required")
    return _section(
        instrument_id,
        bar_type,
        indicators=[_indicator("rsi", "rsi", {"period": p["period"]})],
        long_entry={
            "all": [
                {"gt": ["indicator:rsi:value", p["oversold"]]},
                {"lt": ["indicator:rsi:value", p["mid_level"]]},
            ]
        },
        long_exit={"gt": ["indicator:rsi:value", p["overbought"]]},
        short_entry={
            "all": [
                {"lt": ["indicator:rsi:value", p["overbought"]]},
                {"gt": ["indicator:rsi:value", p["mid_level"]]},
            ]
        },
        short_exit={"lt": ["indicator:rsi:value", p["oversold"]]},
    )


BOLLINGER_PARAMS = (
    ParamDef("period", "Band period", "int", 20, 2, 500, 1),
    ParamDef("k", "Std-dev multiplier", "float", 2.0, 0.1, 10.0, 0.1),
)


def bollinger_reversion(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fade a band excursion, exit on the return to the middle band."""
    p = _params(BOLLINGER_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[_indicator("bollinger", "bollinger", {"period": p["period"], "k": p["k"]})],
        long_entry={"lt": ["close", "indicator:bollinger:lower"]},
        long_exit={"crosses_above": ["close", "indicator:bollinger:middle"]},
        short_entry={"gt": ["close", "indicator:bollinger:upper"]},
        short_exit={"crosses_below": ["close", "indicator:bollinger:middle"]},
    )


MACD_PARAMS = (
    ParamDef("fast", "Fast period", "int", 12, 2, 200, 1),
    ParamDef("slow", "Slow period", "int", 26, 3, 400, 1),
    ParamDef("signal", "Signal period", "int", 9, 2, 100, 1),
)


def macd_trend(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """MACD histogram sign confirmed by the MACD/signal cross."""
    p = _params(MACD_PARAMS, params)
    if p["fast"] >= p["slow"]:
        raise ValueError("fast must be smaller than slow")
    return _section(
        instrument_id,
        bar_type,
        indicators=[_indicator("macd", "macd", p)],
        long_entry={
            "all": [
                {"gt": ["indicator:macd:histogram", 0]},
                {"crosses_above": ["indicator:macd:value", "indicator:macd:signal"]},
            ]
        },
        long_exit={
            "any": [
                {"lt": ["indicator:macd:histogram", 0]},
                {"crosses_below": ["indicator:macd:value", "indicator:macd:signal"]},
            ]
        },
        short_entry={
            "all": [
                {"lt": ["indicator:macd:histogram", 0]},
                {"crosses_below": ["indicator:macd:value", "indicator:macd:signal"]},
            ]
        },
        short_exit={
            "any": [
                {"gt": ["indicator:macd:histogram", 0]},
                {"crosses_above": ["indicator:macd:value", "indicator:macd:signal"]},
            ]
        },
    )


DONCHIAN_PARAMS = (ParamDef("period", "Channel period", "int", 20, 2, 500, 1),)


def donchian_breakout(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Close at the rolling N-bar extreme, exit at the channel midpoint."""
    p = _params(DONCHIAN_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[_indicator("donchian", "donchian", {"period": p["period"]})],
        long_entry={"gte": ["close", "indicator:donchian:upper"]},
        long_exit={"lte": ["close", "indicator:donchian:middle"]},
        short_entry={"lte": ["close", "indicator:donchian:lower"]},
        short_exit={"gte": ["close", "indicator:donchian:middle"]},
    )


ATR_PARAMS = (
    ParamDef("atr_period", "ATR period", "int", 14, 2, 200, 1),
    ParamDef("atr_baseline_period", "ATR baseline period", "int", 50, 3, 400, 1),
    ParamDef("trend_period", "Trend SMA period", "int", 20, 2, 400, 1),
)


def atr_vol_breakout(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Volatility expansion above its own baseline, in the trend direction."""
    p = _params(ATR_PARAMS, params)
    if p["atr_period"] >= p["atr_baseline_period"]:
        raise ValueError("atr_period must be smaller than atr_baseline_period")
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("atr_fast", "atr", {"period": p["atr_period"]}),
            _indicator("atr_baseline", "atr", {"period": p["atr_baseline_period"]}),
            _indicator("sma_trend", "sma", {"period": p["trend_period"]}),
        ],
        long_entry={
            "all": [
                {"gt": ["indicator:atr_fast:value", "indicator:atr_baseline:value"]},
                {"gt": ["close", "indicator:sma_trend:value"]},
            ]
        },
        long_exit={"lt": ["close", "indicator:sma_trend:value"]},
        short_entry={
            "all": [
                {"gt": ["indicator:atr_fast:value", "indicator:atr_baseline:value"]},
                {"lt": ["close", "indicator:sma_trend:value"]},
            ]
        },
        short_exit={"gt": ["close", "indicator:sma_trend:value"]},
    )


VWAP_PARAMS = (ParamDef("rsi_period", "RSI filter period", "int", 14, 2, 200, 1),)


def vwap_reversion(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fade a cross away from VWAP while RSI confirms the stretched side."""
    p = _params(VWAP_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("vwap", "vwap", {}),
            _indicator("rsi", "rsi", {"period": p["rsi_period"]}),
        ],
        long_entry={
            "all": [
                {"crosses_below": ["close", "indicator:vwap:value"]},
                {"lt": ["indicator:rsi:value", 50]},
            ]
        },
        long_exit={"crosses_above": ["close", "indicator:vwap:value"]},
        short_entry={
            "all": [
                {"crosses_above": ["close", "indicator:vwap:value"]},
                {"gt": ["indicator:rsi:value", 50]},
            ]
        },
        short_exit={"crosses_below": ["close", "indicator:vwap:value"]},
    )


def buy_and_hold(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Buy the primary token once and hold it to expiry."""
    _params((), params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[],
        long_entry={"gt": ["close", 0]},
        direction="long",
        settlement="hold_to_expiry",
    )


RANDOM_PARAMS = (ParamDef("period", "Block length (bars)", "int", 10, 2, 500, 1),)


def random_entry(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Deterministic alternating mean-cross null baseline.

    No random number is drawn at runtime.  Direction alternates with a fixed
    N-bar mean, so the strategy is reproducible and exists only as a null
    comparison — it is not a trading hypothesis.
    """
    p = _params(RANDOM_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[_indicator("block_mean", "sma", {"period": p["period"]})],
        long_entry={"gt": ["close", "indicator:block_mean:value"]},
        long_exit={"lt": ["close", "indicator:block_mean:value"]},
        short_entry={"lt": ["close", "indicator:block_mean:value"]},
        short_exit={"gt": ["close", "indicator:block_mean:value"]},
    )


FLOW_MOMENTUM_PARAMS = (
    ParamDef("mom_period", "Momentum period", "int", 15, 2, 300, 1),
    ParamDef("flow_period", "Flow lookback", "int", 15, 2, 300, 1),
    ParamDef("min_move", "Min probability move", "float", 0.02, 0.001, 0.40, 0.005),
    ParamDef("min_imbalance", "Min flow imbalance", "float", 0.15, 0.01, 0.95, 0.05),
)


def flow_momentum(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Probability momentum confirmed by signed dollar flow imbalance."""
    p = _params(FLOW_MOMENTUM_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("dp", "prob_change", {"period": p["mom_period"]}),
            _indicator("flow", "flow_imbalance", {"period": p["flow_period"]}),
        ],
        long_entry={
            "all": [
                {"gte": ["indicator:dp:value", p["min_move"]]},
                {"gte": ["indicator:flow:value", p["min_imbalance"]]},
            ]
        },
        long_exit={
            "any": [
                {"lte": ["indicator:dp:value", 0.0]},
                {"lte": ["indicator:flow:value", 0.0]},
            ]
        },
        short_entry={
            "all": [
                {"lte": ["indicator:dp:value", -p["min_move"]]},
                {"lte": ["indicator:flow:value", -p["min_imbalance"]]},
            ]
        },
        short_exit={
            "any": [
                {"gte": ["indicator:dp:value", 0.0]},
                {"gte": ["indicator:flow:value", 0.0]},
            ]
        },
    )


LARGE_TRADE_PARAMS = (
    ParamDef("vol_period", "Volume lookback", "int", 30, 5, 300, 1),
    ParamDef("flow_period", "Flow window", "int", 5, 2, 120, 1),
    ParamDef("volume_multiple", "Min volume / mean ratio", "float", 2.5, 1.1, 20.0, 0.1),
    ParamDef("flow_threshold", "Min signed flow imbalance", "float", 0.25, 0.05, 0.95, 0.05),
)


def large_trade_follow(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Follow relative volume surges confirmed by aggressor-signed flow."""
    p = _params(LARGE_TRADE_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("rvol", "rolling_volume", {"period": p["vol_period"]}),
            _indicator("flow", "flow_imbalance", {"period": p["flow_period"]}),
            _indicator("sma_exit", "sma", {"period": p["vol_period"]}),
        ],
        long_entry={
            "all": [
                {"gte": ["indicator:rvol:ratio", p["volume_multiple"]]},
                {"gte": ["indicator:flow:value", p["flow_threshold"]]},
            ]
        },
        long_exit={
            "any": [
                {"lt": ["close", "indicator:sma_exit:value"]},
                {"lte": ["indicator:flow:value", -p["flow_threshold"]]},
            ]
        },
        short_entry={
            "all": [
                {"gte": ["indicator:rvol:ratio", p["volume_multiple"]]},
                {"lte": ["indicator:flow:value", -p["flow_threshold"]]},
            ]
        },
        short_exit={
            "any": [
                {"gt": ["close", "indicator:sma_exit:value"]},
                {"gte": ["indicator:flow:value", p["flow_threshold"]]},
            ]
        },
    )


SHORT_HORIZON_REVERSION_PARAMS = (
    ParamDef("period", "Lookback period", "int", 15, 3, 240, 1),
    ParamDef("shock_move", "Min shock move", "float", 0.03, 0.005, 0.40, 0.005),
    ParamDef("low_zone", "Lower range zone", "float", 0.20, 0.05, 0.45, 0.05),
    ParamDef("high_zone", "Upper range zone", "float", 0.80, 0.55, 0.95, 0.05),
)


def short_horizon_reversion(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fade a sharp probability dislocation at a recent range extreme."""
    p = _params(SHORT_HORIZON_REVERSION_PARAMS, params)
    if p["low_zone"] >= p["high_zone"]:
        raise ValueError("low_zone must be smaller than high_zone")
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("dp", "prob_change", {"period": p["period"]}),
            _indicator("ext", "extreme_dist", {"period": p["period"]}),
            _indicator("mid_sma", "sma", {"period": p["period"]}),
        ],
        long_entry={
            "all": [
                {"lte": ["indicator:dp:value", -p["shock_move"]]},
                {"lte": ["indicator:ext:position", p["low_zone"]]},
            ]
        },
        long_exit={"gte": ["close", "indicator:mid_sma:value"]},
        short_entry={
            "all": [
                {"gte": ["indicator:dp:value", p["shock_move"]]},
                {"gte": ["indicator:ext:position", p["high_zone"]]},
            ]
        },
        short_exit={"lte": ["close", "indicator:mid_sma:value"]},
    )


LOGIT_REVERSION_PARAMS = (
    ParamDef("rsi_period", "RSI period", "int", 14, 2, 200, 1),
    ParamDef("sma_period", "Mean period", "int", 20, 2, 300, 1),
    ParamDef("logit_bound", "Abs logit bound", "float", 1.5, 0.2, 5.0, 0.1),
    ParamDef("eps", "Logit clip eps", "float", 0.001, 0.0001, 0.05, 0.0005),
)


def logit_reversion(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fade clipped log-odds excursions with an RSI extreme filter."""
    p = _params(LOGIT_REVERSION_PARAMS, params)
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("log_p", "logit", {"eps": p["eps"]}),
            _indicator("rsi", "rsi", {"period": p["rsi_period"]}),
            _indicator("mean_p", "sma", {"period": p["sma_period"]}),
        ],
        long_entry={
            "all": [
                {"lte": ["indicator:log_p:value", -p["logit_bound"]]},
                {"lt": ["indicator:rsi:value", 35.0]},
            ]
        },
        long_exit={"gte": ["close", "indicator:mean_p:value"]},
        short_entry={
            "all": [
                {"gte": ["indicator:log_p:value", p["logit_bound"]]},
                {"gt": ["indicator:rsi:value", 65.0]},
            ]
        },
        short_exit={"lte": ["close", "indicator:mean_p:value"]},
    )


PROB_VOL_BREAKOUT_PARAMS = (
    ParamDef("fast_vol_period", "Fast vol period", "int", 10, 2, 120, 1),
    ParamDef("slow_vol_period", "Baseline vol period", "int", 40, 5, 400, 1),
    ParamDef("range_period", "Range period", "int", 20, 3, 300, 1),
)


def prob_vol_breakout(
    instrument_id: str, bar_type: str = REFERENCE_BAR_TYPE, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Probability volatility expansion with a range breakout."""
    p = _params(PROB_VOL_BREAKOUT_PARAMS, params)
    if p["fast_vol_period"] >= p["slow_vol_period"]:
        raise ValueError("fast_vol_period must be smaller than slow_vol_period")
    return _section(
        instrument_id,
        bar_type,
        indicators=[
            _indicator("pvol_fast", "prob_vol", {"period": p["fast_vol_period"]}),
            _indicator("pvol_slow", "prob_vol", {"period": p["slow_vol_period"]}),
            _indicator("ext", "extreme_dist", {"period": p["range_period"]}),
            _indicator("trend_sma", "sma", {"period": p["range_period"]}),
        ],
        long_entry={
            "all": [
                {"gt": ["indicator:pvol_fast:value", "indicator:pvol_slow:value"]},
                {"gte": ["indicator:ext:position", 0.90]},
            ]
        },
        long_exit={"lt": ["close", "indicator:trend_sma:value"]},
        short_entry={
            "all": [
                {"gt": ["indicator:pvol_fast:value", "indicator:pvol_slow:value"]},
                {"lte": ["indicator:ext:position", 0.10]},
            ]
        },
        short_exit={"gt": ["close", "indicator:trend_sma:value"]},
    )


# ---------------------------------------------------------------------------
# Catalogue


@dataclass(frozen=True)
class Builtin:
    id: str
    label: str
    description: str
    provenance: str
    params: tuple[ParamDef, ...]
    builder: Callable[..., dict[str, Any]]
    null_baseline: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def build(
        self,
        instrument_id: str,
        bar_type: str = REFERENCE_BAR_TYPE,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        rules = self.builder(instrument_id, bar_type, params)
        _validate_section(self.id, rules)
        return rules

    def to_json(self, instrument_id: str, bar_type: str) -> dict[str, Any]:
        rules = self.build(instrument_id, bar_type)
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "provenance": self.provenance,
            "null_baseline": self.null_baseline,
            "tags": list(self.tags),
            "params": [p.to_json() for p in self.params],
            "defaults": self.defaults(),
            "direction": rules_direction(rules),
            "settlement": rules["settlement"],
            "rules": rules,
        }


BUILTINS: tuple[Builtin, ...] = (
    Builtin(
        id="nautilus_ema_cross",
        label="EMA cross",
        description="Fast EMA crossing the slow EMA; reverses on the opposite cross.",
        provenance="NautilusTrader EMACross example",
        params=EMACROSS_PARAMS,
        builder=nautilus_ema_cross,
        tags=("trend",),
    ),
    Builtin(
        id="sma_trend",
        label="SMA trend",
        description="Price above a trend SMA, with the trend above a slower filter.",
        provenance="sibling strategy momentum_trend_follow",
        params=SMA_TREND_PARAMS,
        builder=sma_trend,
        tags=("trend",),
    ),
    Builtin(
        id="rsi_reversion",
        label="RSI reversion",
        description="Buy the oversold recovery; fade the overbought recovery.",
        provenance="sibling strategy momentum_rsi_recovery",
        params=RSI_PARAMS,
        builder=rsi_reversion,
        tags=("mean_reversion",),
    ),
    Builtin(
        id="bollinger_reversion",
        label="Bollinger reversion",
        description="Enter on a band excursion, exit on the return to the middle band.",
        provenance="sibling strategy mean_reversion_pct_band_flip",
        params=BOLLINGER_PARAMS,
        builder=bollinger_reversion,
        tags=("mean_reversion",),
    ),
    Builtin(
        id="macd_trend",
        label="MACD trend",
        description="MACD histogram sign confirmed by the MACD/signal line cross.",
        provenance="sibling strategy momentum_macd_histogram",
        params=MACD_PARAMS,
        builder=macd_trend,
        tags=("trend", "momentum"),
    ),
    Builtin(
        id="donchian_breakout",
        label="Donchian breakout",
        description="Close at the rolling N-bar high/low, exit at the channel midpoint.",
        provenance="sibling strategy momentum_breakout_band",
        params=DONCHIAN_PARAMS,
        builder=donchian_breakout,
        tags=("breakout",),
    ),
    Builtin(
        id="atr_vol_breakout",
        label="ATR volatility breakout",
        description="ATR expanding above its own baseline, traded with the trend.",
        provenance="sibling strategy vol_compression_breakout",
        params=ATR_PARAMS,
        builder=atr_vol_breakout,
        tags=("breakout", "volatility"),
    ),
    Builtin(
        id="vwap_reversion",
        label="VWAP reversion",
        description="Fade a cross away from VWAP when RSI confirms the stretched side.",
        provenance="sibling strategy mean_reversion_volume_reverted",
        params=VWAP_PARAMS,
        builder=vwap_reversion,
        tags=("mean_reversion",),
    ),
    Builtin(
        id="buy_and_hold",
        label="Buy and hold",
        description="Buy the primary token at the first bar and hold it to expiry.",
        provenance="sibling strategy always_back baseline",
        params=(),
        builder=buy_and_hold,
        tags=("baseline",),
    ),
    Builtin(
        id="random_entry",
        label="Alternating null baseline",
        description=(
            "Deterministic alternating N-bar mean cross. A null baseline for "
            "comparison only; no randomness at runtime and no trading hypothesis."
        ),
        provenance="sibling strategy pseudo_random_sides baseline",
        params=RANDOM_PARAMS,
        builder=random_entry,
        null_baseline=True,
        tags=("baseline", "null"),
    ),
    Builtin(
        id="flow_momentum",
        label="Flow-Confirmed Momentum",
        description=(
            "Enters when probability change over the lookback exceeds the minimum "
            "move and signed dollar flow imbalance confirms the direction."
        ),
        provenance=(
            "FundedBetBacktesting/src/polymarket_research/strategies/"
            "flow_momentum.py (flow_confirmed_momentum)"
        ),
        params=FLOW_MOMENTUM_PARAMS,
        builder=flow_momentum,
        tags=("prediction_market", "flow", "momentum"),
    ),
    Builtin(
        id="large_trade_follow",
        label="Large Trade Flow Follow",
        description=(
            "Follows relative volume surges when bar volume exceeds a multiple of "
            "rolling mean volume and signed flow confirms aggressor direction."
        ),
        provenance=(
            "FundedBetBacktesting/src/polymarket_research/strategies/"
            "large_trade.py (large_trade_signal) & smart_money.py"
        ),
        params=LARGE_TRADE_PARAMS,
        builder=large_trade_follow,
        tags=("prediction_market", "flow", "microstructure"),
    ),
    Builtin(
        id="short_horizon_reversion",
        label="Short-Horizon Probability Reversion",
        description=(
            "Fades sharp short-horizon probability dislocations when recent range "
            "position reaches an extreme and signed flow begins reverting."
        ),
        provenance=(
            "FundedBetBacktesting/src/polymarket_research/backtest/"
            "reversion_optimizer.py & docs/polymarket_strategy_register.md "
            "(short_horizon_reversion)"
        ),
        params=SHORT_HORIZON_REVERSION_PARAMS,
        builder=short_horizon_reversion,
        tags=("prediction_market", "mean_reversion", "probability"),
    ),
    Builtin(
        id="logit_reversion",
        label="Logit Odds Mean Reversion",
        description=(
            "Trades log-odds (clipped logit) excursions paired with RSI "
            "oversold/overbought thresholds so signals scale symmetrically near "
            "0 and 1."
        ),
        provenance=(
            "FundedBetBacktesting/src/betting_signals/features/probability.py "
            "(log_edge) & mean_reversion"
        ),
        params=LOGIT_REVERSION_PARAMS,
        builder=logit_reversion,
        tags=("prediction_market", "mean_reversion", "logit"),
    ),
    Builtin(
        id="prob_vol_breakout",
        label="Probability Volatility Regime Breakout",
        description=(
            "Enters when fast probability volatility expands above baseline "
            "probability volatility and price breaks out of the recent "
            "probability range."
        ),
        provenance=(
            "FundedBetBacktesting/src/polymarket_research/backtest/"
            "breakout_optimizer.py & regime_grid.py"
        ),
        params=PROB_VOL_BREAKOUT_PARAMS,
        builder=prob_vol_breakout,
        tags=("prediction_market", "volatility", "breakout"),
    ),
)

_BY_ID = {builtin.id: builtin for builtin in BUILTINS}


def rules_direction(rules: Mapping[str, Any]) -> str:
    return str(rules.get("direction") or "both")


def get_builtin(builtin_id: str) -> Builtin:
    try:
        return _BY_ID[builtin_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown built-in strategy {builtin_id!r}; expected one of {sorted(_BY_ID)}"
        ) from exc


def build(
    builtin_id: str,
    instrument_id: str,
    bar_type: str = REFERENCE_BAR_TYPE,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one catalogue entry's rule section, validated."""
    return get_builtin(builtin_id).build(instrument_id, bar_type, params)


def _reference_run_config(strategy: Mapping[str, Any]) -> RunConfig:
    return RunConfig(
        strategy=StrategySection(**strategy),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        evaluation=EvaluationConfig(date_range=DateRange(start="2024-10-01", end="2026-10-01")),
    )


def _validate_section(builtin_id: str, strategy: Mapping[str, Any]) -> None:
    try:
        _reference_run_config(strategy)
    except Exception as exc:  # noqa: BLE001 - surface the built-in that is broken
        raise ValueError(f"built-in {builtin_id!r} does not validate: {exc}") from exc


def catalogue(
    instrument_id: str = REFERENCE_INSTRUMENT_ID,
    bar_type: str = REFERENCE_BAR_TYPE,
) -> list[dict[str, Any]]:
    """Metadata, defaults and validated rules for every built-in."""
    entries: list[dict[str, Any]] = []
    for builtin in BUILTINS:
        rules = builtin.build(instrument_id, bar_type)
        # Defence in depth: the catalogue never ships an unvalidated rule set.
        _validate_section(builtin.id, rules)
        entries.append(
            {
                "id": builtin.id,
                "label": builtin.label,
                "description": builtin.description,
                "provenance": builtin.provenance,
                "null_baseline": builtin.null_baseline,
                "tags": list(builtin.tags),
                "params": [p.to_json() for p in builtin.params],
                "parameters": [p.to_json() for p in builtin.params],
                "defaults": builtin.defaults(),
                "direction": rules_direction(rules),
                "settlement": rules["settlement"],
                "rules": rules,
            }
        )
    return entries


__all__ = [
    "ATR_PARAMS",
    "BOLLINGER_PARAMS",
    "BUILTINS",
    "Builtin",
    "CATALOGUE_VERSION",
    "DONCHIAN_PARAMS",
    "EMACROSS_PARAMS",
    "FLOW_MOMENTUM_PARAMS",
    "LARGE_TRADE_PARAMS",
    "LOGIT_REVERSION_PARAMS",
    "MACD_PARAMS",
    "PROB_VOL_BREAKOUT_PARAMS",
    "ParamDef",
    "RANDOM_PARAMS",
    "RSI_PARAMS",
    "REFERENCE_BAR_TYPE",
    "REFERENCE_INSTRUMENT_ID",
    "SHORT_HORIZON_REVERSION_PARAMS",
    "SMA_TREND_PARAMS",
    "VWAP_PARAMS",
    "atr_vol_breakout",
    "bollinger_reversion",
    "build",
    "buy_and_hold",
    "catalogue",
    "donchian_breakout",
    "flow_momentum",
    "get_builtin",
    "large_trade_follow",
    "logit_reversion",
    "macd_trend",
    "nautilus_ema_cross",
    "prob_vol_breakout",
    "random_entry",
    "rsi_reversion",
    "short_horizon_reversion",
    "sma_trend",
    "vwap_reversion",
]
