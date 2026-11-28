"""First-principles reference implementations used only by the tests.

These are deliberately independent of NautilusTrader: plain Python arithmetic
straight from the textbook definition of each indicator.  They exist so a
parity test can compare two implementations, not one implementation with
itself.  Warm-up entries are ``None``; the first computed value is always a
``float``.

Definitions pinned here (matching the conventions the adapter documents):

* EMA is seeded with the simple mean of the first ``period`` values and then
  applies ``alpha = 2 / (period + 1)``.
* RSI and ATR use Wilder smoothing with a simple mean as the seed.
* Bollinger bands use the population standard deviation over the window.
* MACD uses SMA-seeded EMAs; the signal EMA is seeded from the first
  ``signal`` MACD values.
* Stochastics: raw %K over ``period_k``, then a simple moving average of
  ``slowing`` bars for %K, then a simple moving average of ``period_d`` bars
  for %D.
* VWAP is the cumulative ``sum(close * volume) / sum(volume)`` and stays
  ``None`` until some volume has been seen.
* The prediction-market additions follow :mod:`docs/BRIEF.md` §7: the clipped
  logit uses ``ln(p / (1 - p))`` with ``p`` clipped to ``[eps, 1 - eps]``;
  probability volatility is the sample (``ddof=1``) standard deviation of
  one-bar changes; the flow imbalance signs each bar by ``close`` vs ``open``
  (falling back to ``close`` vs the previous close), never by tick rule.
"""

from __future__ import annotations

import math
from typing import Sequence

Values = list[float | None]
Bars = Sequence[dict[str, float]]


def sma(values: Sequence[float], period: int) -> Values:
    out: Values = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out


def wma(values: Sequence[float], period: int) -> Values:
    denominator = period * (period + 1) / 2
    out: Values = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        out[i] = sum((j + 1) * value for j, value in enumerate(window)) / denominator
    return out


def ema(values: Sequence[float], period: int) -> Values:
    alpha = 2.0 / (period + 1.0)
    out: Values = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = alpha * values[i] + (1.0 - alpha) * current
        out[i] = current
    return out


def rsi(values: Sequence[float], period: int) -> Values:
    out: Values = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [0.0] * (len(values) - 1)
    losses = [0.0] * (len(values) - 1)
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains[i - 1] = max(change, 0.0)
        losses[i - 1] = max(-change, 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def macd(
    values: Sequence[float], fast: int, slow: int, signal: int
) -> dict[str, Values]:
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: Values = [None] * len(values)
    for i in range(len(values)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            line[i] = fast_ema[i] - slow_ema[i]
    first = next(i for i, value in enumerate(line) if value is not None)
    tail = [value for value in line[first:] if value is not None]
    signal_tail = ema(tail, signal)
    signal_values: Values = [None] * len(values)
    for offset, value in enumerate(signal_tail):
        if value is not None:
            signal_values[first + offset] = value

    # NautilusTrader exposes one ``initialized`` flag per indicator, so the
    # adapter reports None for every output until the signal line is ready.
    ready = next(i for i, value in enumerate(signal_values) if value is not None)
    gated_line: Values = [line[i] if i >= ready else None for i in range(len(values))]
    histogram: Values = [
        None
        if gated_line[i] is None or signal_values[i] is None
        else gated_line[i] - signal_values[i]
        for i in range(len(values))
    ]
    return {"value": gated_line, "signal": signal_values, "histogram": histogram}


def bollinger(
    values: Sequence[float], period: int, k: float
) -> dict[str, Values]:
    middle = sma(values, period)
    upper: Values = [None] * len(values)
    lower: Values = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = middle[i]
        assert mean is not None
        variance = sum((value - mean) ** 2 for value in window) / period
        deviation = math.sqrt(variance)
        upper[i] = mean + k * deviation
        lower[i] = mean - k * deviation
    return {"upper": upper, "middle": middle, "lower": lower}


def true_ranges(bars: Bars) -> list[float]:
    out: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            out.append(bar["high"] - bar["low"])
        else:
            previous_close = bars[i - 1]["close"]
            out.append(
                max(
                    bar["high"] - bar["low"],
                    abs(bar["high"] - previous_close),
                    abs(bar["low"] - previous_close),
                )
            )
    return out


def atr(bars: Bars, period: int) -> Values:
    ranges = true_ranges(bars)
    out: Values = [None] * len(bars)
    if len(ranges) < period:
        return out
    current = sum(ranges[:period]) / period
    out[period - 1] = current
    for i in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[i]) / period
        out[i] = current
    return out


def stochastic(
    bars: Bars, period_k: int, slowing: int, period_d: int
) -> dict[str, Values]:
    count = len(bars)
    raw: Values = [None] * count
    for i in range(period_k - 1, count):
        window = bars[i - period_k + 1 : i + 1]
        highest = max(bar["high"] for bar in window)
        lowest = min(bar["low"] for bar in window)
        if highest == lowest:
            raw[i] = 0.0
        else:
            raw[i] = 100.0 * (bars[i]["close"] - lowest) / (highest - lowest)
    k_values = _rolling_mean_of_optional(raw, slowing)
    d_values = _rolling_mean_of_optional(k_values, period_d)

    # %K exists one ``period_d`` window before %D does; NautilusTrader only
    # reports the oscillator as initialized when %D is ready, so the adapter
    # nulls both outputs before that point.
    ready = next(i for i, value in enumerate(d_values) if value is not None)
    gated_k: Values = [k_values[i] if i >= ready else None for i in range(count)]
    return {"k": gated_k, "d": d_values}


def _rolling_mean_of_optional(values: Values, period: int) -> Values:
    out: Values = [None] * len(values)
    for i in range(len(values)):
        window = values[max(0, i - period + 1) : i + 1]
        if len(window) < period or any(value is None for value in window):
            continue
        out[i] = math.fsum(value for value in window if value is not None) / period
    return out


def donchian(bars: Bars, period: int) -> dict[str, Values]:
    count = len(bars)
    upper: Values = [None] * count
    middle: Values = [None] * count
    lower: Values = [None] * count
    for i in range(period - 1, count):
        window = bars[i - period + 1 : i + 1]
        highest = max(bar["high"] for bar in window)
        lowest = min(bar["low"] for bar in window)
        upper[i] = highest
        lower[i] = lowest
        middle[i] = (highest + lowest) / 2.0
    return {"upper": upper, "middle": middle, "lower": lower}


def vwap(bars: Bars) -> Values:
    out: Values = [None] * len(bars)
    cumulative_value = 0.0
    cumulative_volume = 0.0
    for i, bar in enumerate(bars):
        cumulative_value += bar["close"] * bar["volume"]
        cumulative_volume += bar["volume"]
        if cumulative_volume > 0.0:
            out[i] = cumulative_value / cumulative_volume
    return out


def logit(values: Sequence[float], eps: float) -> Values:
    out: Values = [None] * len(values)
    for i, value in enumerate(values):
        probability = min(max(float(value), eps), 1.0 - eps)
        out[i] = math.log(probability / (1.0 - probability))
    return out


def probability_change(values: Sequence[float], period: int) -> dict[str, Values]:
    value: Values = [None] * len(values)
    pct: Values = [None] * len(values)
    for i in range(period, len(values)):
        previous = values[i - period]
        current = values[i]
        value[i] = current - previous
        pct[i] = ((current - previous) / previous * 100.0) if previous != 0.0 else 0.0
    return {"value": value, "pct": pct}


def probability_volatility(values: Sequence[float], period: int) -> Values:
    out: Values = [None] * len(values)
    for i in range(period, len(values)):
        changes = [values[j] - values[j - 1] for j in range(i - period + 1, i + 1)]
        mean = sum(changes) / period
        variance = sum((change - mean) ** 2 for change in changes) / (period - 1)
        out[i] = math.sqrt(max(variance, 0.0))
    return out


def extreme_distance(bars: Bars, period: int) -> dict[str, Values]:
    count = len(bars)
    from_high: Values = [None] * count
    from_low: Values = [None] * count
    position: Values = [None] * count
    for i in range(period - 1, count):
        window = bars[i - period + 1 : i + 1]
        highest = max(bar["high"] for bar in window)
        lowest = min(bar["low"] for bar in window)
        close = bars[i]["close"]
        span = highest - lowest
        from_high[i] = highest - close
        from_low[i] = close - lowest
        position[i] = ((close - lowest) / span) if span > 0.0 else 0.5
    return {"from_high": from_high, "from_low": from_low, "position": position}


def rolling_volume(bars: Bars, period: int) -> dict[str, Values]:
    count = len(bars)
    mean_values: Values = [None] * count
    total_values: Values = [None] * count
    ratio_values: Values = [None] * count
    for i in range(period - 1, count):
        window = [bar["volume"] for bar in bars[i - period + 1 : i + 1]]
        total = sum(window)
        mean = total / period
        mean_values[i] = mean
        total_values[i] = total
        ratio_values[i] = (window[-1] / mean) if mean > 0.0 else 1.0
    return {"value": mean_values, "sum": total_values, "ratio": ratio_values}


def flow_imbalance(bars: Bars, period: int) -> dict[str, Values]:
    count = len(bars)
    value: Values = [None] * count
    signed_volume: Values = [None] * count
    window: list[tuple[float, float]] = []
    for i, bar in enumerate(bars):
        open_ = bar["open"]
        close = bar["close"]
        if close > open_:
            sign = 1.0
        elif close < open_:
            sign = -1.0
        else:
            previous_close = bars[i - 1]["close"] if i > 0 else open_
            if close > previous_close:
                sign = 1.0
            elif close < previous_close:
                sign = -1.0
            else:
                sign = 0.0
        window.append((sign * bar["volume"], bar["volume"]))
        if len(window) > period:
            window.pop(0)
        if len(window) == period:
            net_signed = sum(signed for signed, _ in window)
            gross = sum(traded for _, traded in window)
            value[i] = (net_signed / gross) if gross > 0.0 else 0.0
            signed_volume[i] = net_signed
    return {"value": value, "signed_volume": signed_volume}


def reference_series(
    indicator_id: str, params: dict[str, float], bars: Bars
) -> dict[str, Values]:
    """Reference output for one indicator id and validated parameter set."""
    closes = [bar["close"] for bar in bars]
    if indicator_id == "sma":
        return {"value": sma(closes, int(params["period"]))}
    if indicator_id == "ema":
        return {"value": ema(closes, int(params["period"]))}
    if indicator_id == "wma":
        return {"value": wma(closes, int(params["period"]))}
    if indicator_id == "rsi":
        return {"value": rsi(closes, int(params["period"]))}
    if indicator_id == "macd":
        return macd(
            closes,
            int(params["fast"]),
            int(params["slow"]),
            int(params["signal"]),
        )
    if indicator_id == "bollinger":
        return bollinger(closes, int(params["period"]), float(params["k"]))
    if indicator_id == "atr":
        return {"value": atr(bars, int(params["period"]))}
    if indicator_id == "stochastic":
        return stochastic(
            bars,
            int(params["period_k"]),
            int(params["slowing"]),
            int(params["period_d"]),
        )
    if indicator_id == "donchian":
        return donchian(bars, int(params["period"]))
    if indicator_id == "vwap":
        return {"value": vwap(bars)}
    if indicator_id == "logit":
        return {"value": logit(closes, float(params["eps"]))}
    if indicator_id == "prob_change":
        return probability_change(closes, int(params["period"]))
    if indicator_id == "prob_vol":
        return {"value": probability_volatility(closes, int(params["period"]))}
    if indicator_id == "extreme_dist":
        return extreme_distance(bars, int(params["period"]))
    if indicator_id == "rolling_volume":
        return rolling_volume(bars, int(params["period"]))
    if indicator_id == "flow_imbalance":
        return flow_imbalance(bars, int(params["period"]))
    raise ValueError(f"no reference implementation for '{indicator_id}'")
