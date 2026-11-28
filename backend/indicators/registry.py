"""Indicator registry: the single source of parameter and output metadata.

The chart and the backtest both consume this registry, and both consume the
same Python/Nautilus implementation in :mod:`backend.indicators.compute`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Pane = Literal["overlay", "separate"]


@dataclass(frozen=True)
class OutputSpec:
    key: str
    label: str


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    type: Literal["int", "float", "choice", "bool"]
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IndicatorSpec:
    id: str
    label: str
    pane: Pane
    inputs: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    outputs: tuple[OutputSpec, ...]
    warmup: Callable[[dict[str, Any]], int]
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}


def _warmup_period(key: str = "period") -> Callable[[dict[str, Any]], int]:
    return lambda params: max(int(params.get(key, 1)) - 1, 0)


#: Declared warm-up for the rolling-window indicators: ``period - 1`` leading nulls.
_period_minus_one: Callable[[dict[str, Any]], int] = _warmup_period()


def _warmup_rsi(params: dict[str, Any]) -> int:
    return int(params["period"])


def _warmup_macd(params: dict[str, Any]) -> int:
    return int(params["slow"]) + int(params["signal"]) - 2


def _warmup_stochastic(params: dict[str, Any]) -> int:
    return int(params["period_k"]) + int(params["slowing"]) + int(params["period_d"]) - 3


_SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        id="sma",
        label="Simple Moving Average",
        pane="overlay",
        inputs=("close",),
        params=(ParamSpec("period", "Period", "int", 20, 2, 500, 1),),
        outputs=(OutputSpec("value", "SMA"),),
        warmup=_warmup_period(),
        description="Arithmetic mean of the last N closes.",
    ),
    IndicatorSpec(
        id="ema",
        label="Exponential Moving Average",
        pane="overlay",
        inputs=("close",),
        params=(ParamSpec("period", "Period", "int", 20, 2, 500, 1),),
        outputs=(OutputSpec("value", "EMA"),),
        warmup=_warmup_period(),
        description="Nautilus ExponentialMovingAverage, seeded by the first value.",
    ),
    IndicatorSpec(
        id="wma",
        label="Weighted Moving Average",
        pane="overlay",
        inputs=("close",),
        params=(ParamSpec("period", "Period", "int", 20, 2, 500, 1),),
        outputs=(OutputSpec("value", "WMA"),),
        warmup=_warmup_period(),
        description="Linearly weighted mean of the last N closes.",
    ),
    IndicatorSpec(
        id="rsi",
        label="Relative Strength Index",
        pane="separate",
        inputs=("close",),
        params=(ParamSpec("period", "Period", "int", 14, 2, 200, 1),),
        outputs=(OutputSpec("value", "RSI"),),
        warmup=_warmup_rsi,
        description="Wilder RSI on close, 0–100.",
    ),
    IndicatorSpec(
        id="macd",
        label="MACD",
        pane="separate",
        inputs=("close",),
        params=(
            ParamSpec("fast", "Fast period", "int", 12, 2, 200, 1),
            ParamSpec("slow", "Slow period", "int", 26, 3, 400, 1),
            ParamSpec("signal", "Signal period", "int", 9, 2, 100, 1),
        ),
        outputs=(
            OutputSpec("value", "MACD"),
            OutputSpec("signal", "Signal"),
            OutputSpec("histogram", "Histogram"),
        ),
        warmup=_warmup_macd,
        description="MACD line, signal line and histogram (12/26/9 default).",
    ),
    IndicatorSpec(
        id="bollinger",
        label="Bollinger Bands",
        pane="overlay",
        inputs=("close",),
        params=(
            ParamSpec("period", "Period", "int", 20, 2, 500, 1),
            ParamSpec("k", "Std dev multiplier", "float", 2.0, 0.1, 10.0, 0.1),
        ),
        outputs=(
            OutputSpec("upper", "Upper"),
            OutputSpec("middle", "Middle"),
            OutputSpec("lower", "Lower"),
        ),
        warmup=_warmup_period(),
        description="SMA middle band with ±k standard deviations.",
    ),
    IndicatorSpec(
        id="atr",
        label="Average True Range",
        pane="separate",
        inputs=("high", "low", "close"),
        params=(ParamSpec("period", "Period", "int", 14, 2, 200, 1),),
        outputs=(OutputSpec("value", "ATR"),),
        warmup=_warmup_period(),
        description="Wilder average true range on bar high/low/close.",
    ),
    IndicatorSpec(
        id="stochastic",
        label="Stochastic Oscillator",
        pane="separate",
        inputs=("high", "low", "close"),
        params=(
            ParamSpec("period_k", "%K period", "int", 14, 2, 200, 1),
            ParamSpec("period_d", "%D period", "int", 3, 1, 50, 1),
            ParamSpec("slowing", "Slowing", "int", 3, 1, 20, 1),
        ),
        outputs=(OutputSpec("k", "%K"), OutputSpec("d", "%D")),
        warmup=_warmup_stochastic,
        description="Stochastic %K and %D, 0–100.",
    ),
    IndicatorSpec(
        id="donchian",
        label="Donchian Channels",
        pane="overlay",
        inputs=("high", "low"),
        params=(ParamSpec("period", "Period", "int", 20, 2, 500, 1),),
        outputs=(
            OutputSpec("upper", "Upper"),
            OutputSpec("middle", "Middle"),
            OutputSpec("lower", "Lower"),
        ),
        warmup=_warmup_period(),
        description="Rolling N-bar high/low channel.",
    ),
    IndicatorSpec(
        id="vwap",
        label="Volume Weighted Average Price",
        pane="overlay",
        inputs=("high", "low", "close", "volume"),
        params=(),
        outputs=(OutputSpec("value", "VWAP"),),
        warmup=lambda _params: 0,
        description="Cumulative VWAP from typical price and volume.",
    ),
    IndicatorSpec(
        id="logit",
        label="Clipped Logit",
        pane="separate",
        inputs=("close",),
        params=(
            ParamSpec("eps", "Clip epsilon", "float", 0.001, 0.0001, 0.1, 0.0001),
        ),
        outputs=(OutputSpec("value", "Logit"),),
        warmup=lambda _params: 0,
        description="Log-odds of the probability, clipped to [eps, 1 - eps].",
    ),
    IndicatorSpec(
        id="prob_change",
        label="Probability Change",
        pane="separate",
        inputs=("close",),
        params=(ParamSpec("period", "Lookback period", "int", 10, 1, 500, 1),),
        outputs=(OutputSpec("value", "Delta p"), OutputSpec("pct", "Pct change")),
        warmup=lambda params: int(params["period"]),
        description="Absolute and percentage change in probability over N bars.",
    ),
    IndicatorSpec(
        id="prob_vol",
        label="Probability Volatility",
        pane="separate",
        inputs=("close",),
        params=(ParamSpec("period", "Lookback period", "int", 20, 2, 500, 1),),
        outputs=(OutputSpec("value", "Rolling std (Delta p)"),),
        warmup=lambda params: int(params["period"]),
        description="Rolling sample standard deviation of one-bar probability changes.",
    ),
    IndicatorSpec(
        id="extreme_dist",
        label="Distance from Extremes",
        pane="separate",
        inputs=("high", "low", "close"),
        params=(ParamSpec("period", "Lookback period", "int", 20, 2, 500, 1),),
        outputs=(
            OutputSpec("from_high", "From high"),
            OutputSpec("from_low", "From low"),
            OutputSpec("position", "Range position"),
        ),
        warmup=_period_minus_one,
        description="Close distance below the N-bar high/low and its range position.",
    ),
    IndicatorSpec(
        id="rolling_volume",
        label="Rolling Volume",
        pane="separate",
        inputs=("volume",),
        params=(ParamSpec("period", "Lookback period", "int", 20, 2, 500, 1),),
        outputs=(
            OutputSpec("value", "Rolling mean volume"),
            OutputSpec("sum", "Rolling total volume"),
            OutputSpec("ratio", "Volume / mean"),
        ),
        warmup=_period_minus_one,
        description="Rolling mean and total volume, and the latest bar's ratio to it.",
    ),
    IndicatorSpec(
        id="flow_imbalance",
        label="Signed Flow Imbalance",
        pane="separate",
        inputs=("open", "close", "volume"),
        params=(ParamSpec("period", "Lookback period", "int", 15, 2, 500, 1),),
        outputs=(
            OutputSpec("value", "Flow imbalance [-1, 1]"),
            OutputSpec("signed_volume", "Net signed volume"),
        ),
        warmup=_period_minus_one,
        description="Net signed volume over gross volume; bar sign from open/close.",
    ),
)

_BY_ID = {spec.id: spec for spec in _SPECS}


def list_specs() -> list[IndicatorSpec]:
    return list(_SPECS)


def get_spec(indicator_id: str) -> IndicatorSpec:
    try:
        return _BY_ID[indicator_id]
    except KeyError as exc:
        raise ValueError(f"unknown indicator '{indicator_id}'") from exc


def validate_params(indicator_id: str, params: dict[str, Any] | None) -> dict[str, Any]:
    spec = get_spec(indicator_id)
    supplied = dict(params or {})
    known = {p.key for p in spec.params}
    unknown = set(supplied) - known
    if unknown:
        raise ValueError(
            f"unknown parameter(s) for '{indicator_id}': {', '.join(sorted(unknown))}"
        )
    out: dict[str, Any] = {}
    for p in spec.params:
        value = supplied.get(p.key, p.default)
        if p.type == "int":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{indicator_id}.{p.key} must be an integer")
            if int(value) != value:
                raise ValueError(f"{indicator_id}.{p.key} must be an integer")
            value = int(value)
        elif p.type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{indicator_id}.{p.key} must be a number")
            value = float(value)
        elif p.type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{indicator_id}.{p.key} must be a boolean")
        elif p.type == "choice":
            if value not in (p.choices or ()):
                raise ValueError(
                    f"{indicator_id}.{p.key} must be one of {p.choices}"
                )
        if p.min is not None and float(value) < p.min:
            raise ValueError(
                f"{indicator_id}.{p.key} must be >= {p.min:g}"
            )
        if p.max is not None and float(value) > p.max:
            raise ValueError(
                f"{indicator_id}.{p.key} must be <= {p.max:g}"
            )
        out[p.key] = value
    # cross-parameter checks
    if indicator_id == "macd" and out["fast"] >= out["slow"]:
        raise ValueError("macd.fast must be smaller than macd.slow")
    return out


def spec_to_json(spec: IndicatorSpec) -> dict[str, Any]:
    params = [
            {
                "key": p.key,
                "name": p.key,
                "label": p.label,
                "type": p.type,
                "default": p.default,
                "min": p.min,
                "max": p.max,
                "step": p.step,
                "choices": list(p.choices) if p.choices else None,
            }
            for p in spec.params
        ]
    return {
        "id": spec.id,
        "label": spec.label,
        "pane": spec.pane,
        "inputs": list(spec.inputs),
        "parameters": params,
        "params": [
            {
                "key": p.key,
                "name": p.key,
                "label": p.label,
                "type": p.type,
                "default": p.default,
                "min": p.min,
                "max": p.max,
                "step": p.step,
                "choices": list(p.choices) if p.choices else None,
            }
            for p in spec.params
        ],
        "outputs": [{"key": o.key, "label": o.label} for o in spec.outputs],
        "warmup": _warmup_for_spec(spec),
        "description": spec.description,
    }


def _warmup_for_spec(spec: IndicatorSpec) -> int:
    try:
        return int(spec.warmup(spec.defaults()))
    except Exception:  # noqa: BLE001
        return 0


def registry_json() -> dict[str, Any]:
    return {"indicators": [spec_to_json(s) for s in _SPECS]}
