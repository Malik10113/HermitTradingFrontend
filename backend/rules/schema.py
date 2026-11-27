"""Declarative strategy schema, validation and canonical hashes.

There is no code generation and no ``eval``.  A strategy is JSON; every
reference is resolved against the indicator registry before a job exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

PRICE_OPERANDS = {"open", "high", "low", "close", "volume", "trade_count"}
LIFECYCLE_OPERANDS = {
    "time_to_kickoff_minutes",
    "time_since_kickoff_minutes",
    "time_to_settlement_minutes",
    "market_status",
}
COMPARATORS = {"gt", "gte", "lt", "lte", "crosses_above", "crosses_below"}
BOOLEAN_NODES = {"all", "any", "not"}
MAX_DEPTH = 8
MAX_NODES = 200

INSTANCE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class IndicatorInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("instance_id")
    @classmethod
    def _instance_id(cls, v: str) -> str:
        if not INSTANCE_RE.match(v):
            raise ValueError("instance_id must match ^[a-z][a-z0-9_]{0,31}$")
        return v


class LifecycleFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phases: list[Literal["pre_match", "in_play", "post_match", "settled"]] | None = None
    require_known_phase: bool = True
    time_to_kickoff_min_minutes: float | None = None
    time_to_kickoff_max_minutes: float | None = None
    time_to_settlement_min_minutes: float | None = None

    @model_validator(mode="after")
    def _ranges(self) -> "LifecycleFilter":
        if (
            self.time_to_kickoff_min_minutes is not None
            and self.time_to_kickoff_max_minutes is not None
            and self.time_to_kickoff_min_minutes > self.time_to_kickoff_max_minutes
        ):
            raise ValueError("time_to_kickoff min must be <= max")
        return self


class StrategySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    bar_type: str = "1m"
    indicators: list[IndicatorInstance] = Field(default_factory=list)
    long_entry: dict[str, Any] | None = None
    long_exit: dict[str, Any] | None = None
    short_entry: dict[str, Any] | None = None
    short_exit: dict[str, Any] | None = None
    direction: Literal["long", "short", "both"] = "both"
    settlement: Literal["hold_to_expiry", "rule_exit"] = "hold_to_expiry"
    lifecycle_filters: LifecycleFilter | None = None
    # Quick-authoring hints; the API expands them into a full rule tree before
    # a job exists and they never enter the hashed strategy section.
    builtin_id: str | None = None
    builtin_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("settlement", mode="before")
    @classmethod
    def _settlement_alias(cls, value: Any) -> Any:
        if value in ("exit_on_signal", "rule_exit"):
            return "rule_exit"
        if value == "hold_to_expiry":
            return "hold_to_expiry"
        return value

    @model_validator(mode="after")
    def _validate_rules(self) -> "StrategySection":
        ids = [i.instance_id for i in self.indicators]
        if len(ids) != len(set(ids)):
            raise ValueError("indicator instance_id values must be unique")
        if self.builtin_id is not None:
            # Quick authoring: the API expands the catalogue entry into rules
            # before a job exists; there is nothing to validate yet.
            return self
        if self.direction in ("long", "both") and self.long_entry is None and self.short_entry is None:
            raise ValueError("at least one entry rule is required")
        if self.direction == "long" and self.long_entry is None:
            raise ValueError("long direction requires long_entry")
        if self.direction == "short" and self.short_entry is None:
            raise ValueError("short direction requires short_entry")
        types = {i.instance_id: i.type for i in self.indicators}
        for field in ("long_entry", "long_exit", "short_entry", "short_exit"):
            tree = getattr(self, field)
            if tree is not None:
                validate_condition(tree, types)
        return self


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["frictionless", "optimistic", "conservative", "stress", "book"] = "conservative"
    fill_timing: Literal["next_bar_open"] = "next_bar_open"
    fee_bps: float | None = Field(default=None, ge=0, le=1000)
    slippage_bps: float | None = Field(default=None, ge=0, le=10_000)
    min_abs_slippage: float | None = Field(default=None, ge=0, le=0.5)
    book_staleness_s: int = Field(default=60, ge=0, le=86_400)
    max_depth_levels: int = Field(default=10, ge=1, le=100)
    fallback: Literal["conservative"] = "conservative"


class SizingConfig(BaseModel):
    mode: Literal["fixed_stake", "risk_pct"] = "fixed_stake"
    value: float = Field(default=100.0, gt=0)
    bankroll_usd: float = Field(default=1000.0, gt=0, validation_alias=AliasChoices("bankroll_usd", "bankroll_gbp"))

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("value")
    @classmethod
    def _risk_pct_range(cls, v: float, info: Any) -> float:
        # The mode is validated after this field; range is enforced below.
        return v

    @model_validator(mode="after")
    def _risk_pct(self) -> "SizingConfig":
        if self.mode == "risk_pct" and self.value > 100:
            raise ValueError("risk_pct value must be <= 100")
        return self


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sizing: SizingConfig = Field(default_factory=SizingConfig)
    max_position: int = Field(default=1, ge=1, le=1)
    max_trades: int = Field(default=10_000, ge=1, le=100_000)


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: str = Field(validation_alias=AliasChoices("start", "from"))
    end: str = Field(validation_alias=AliasChoices("end", "to"))

    @model_validator(mode="after")
    def _order(self) -> "DateRange":
        from datetime import date

        try:
            a = date.fromisoformat(self.start)
            b = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError("dates must be ISO-8601 (YYYY-MM-DD)") from exc
        # A single UTC day is a valid range (one match day); only a reversed
        # range is a request error.
        if a > b:
            raise ValueError("date range start must not be after end")
        return self


class SplitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_end: str
    test_start: str
    embargo_days: int = Field(default=1, ge=0, le=90)

    @model_validator(mode="after")
    def _order(self) -> "SplitConfig":
        from datetime import date, timedelta

        try:
            train_end = date.fromisoformat(self.train_end)
            test_start = date.fromisoformat(self.test_start)
        except ValueError as exc:
            raise ValueError("split dates must be ISO-8601 (YYYY-MM-DD)") from exc
        if test_start <= train_end:
            raise ValueError("test_start must be after train_end")
        if (test_start - train_end).days <= self.embargo_days:
            raise ValueError("embargo_days leaves no gap between train and test")
        _ = timedelta  # keeps the import explicit for future range checks
        return self


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_range: DateRange
    split: SplitConfig | None = None


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: StrategySection
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    evaluation: EvaluationConfig
    # Preflight warnings the user explicitly accepted; recorded in the manifest.
    acknowledged_warnings: list[str] = Field(default_factory=list)

    def canonical_strategy(self) -> dict[str, Any]:
        return self.strategy.model_dump(mode="json", exclude_none=True)

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Rule-tree validation


def validate_condition(tree: dict[str, Any], indicator_types: dict[str, str]) -> None:
    state = {"nodes": 0}
    _walk(tree, indicator_types, depth=0, state=state)
    if state["nodes"] > MAX_NODES:
        raise ValueError(f"rule tree exceeds {MAX_NODES} nodes")


def _walk(
    node: Any,
    indicator_types: dict[str, str],
    *,
    depth: int,
    state: dict[str, int],
) -> None:
    if depth > MAX_DEPTH:
        raise ValueError(f"rule tree exceeds depth {MAX_DEPTH}")
    state["nodes"] += 1
    if state["nodes"] > MAX_NODES:
        raise ValueError(f"rule tree exceeds {MAX_NODES} nodes")
    if not isinstance(node, dict) or len(node) != 1:
        raise ValueError("a condition must be an object with exactly one key")
    key, value = next(iter(node.items()))
    if key in ("all", "any"):
        if not isinstance(value, list) or not value:
            raise ValueError(f"'{key}' requires a non-empty list")
        if len(value) > 50:
            raise ValueError(f"'{key}' has too many children")
        for child in value:
            _walk(child, indicator_types, depth=depth + 1, state=state)
        return
    if key == "not":
        _walk(value, indicator_types, depth=depth + 1, state=state)
        return
    if key not in COMPARATORS:
        raise ValueError(f"unknown condition key '{key}'")
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"'{key}' requires exactly two operands")
    for operand in value:
        _validate_operand(operand, indicator_types)
    if key in ("crosses_above", "crosses_below"):
        if not all(isinstance(o, str) for o in value):
            raise ValueError(f"'{key}' operands must be price or indicator references")


def _validate_operand(operand: Any, indicator_types: dict[str, str]) -> None:
    if isinstance(operand, bool):
        return
    if isinstance(operand, (int, float)):
        return
    if not isinstance(operand, str):
        raise ValueError("operand must be a number, price field or reference")
    if operand in PRICE_OPERANDS:
        return
    if operand in LIFECYCLE_OPERANDS:
        return
    if operand.startswith("indicator:"):
        parts = operand.split(":")
        if len(parts) != 3:
            raise ValueError(f"malformed indicator reference '{operand}'")
        instance_id, output = parts[1], parts[2]
        if instance_id not in indicator_types:
            raise ValueError(f"unknown indicator instance '{instance_id}'")
        from backend.indicators.registry import get_spec

        spec = get_spec(indicator_types[instance_id])
        outputs = {o.key for o in spec.outputs}
        if output not in outputs:
            raise ValueError(
                f"unknown output '{output}' for indicator '{instance_id}' "
                f"(expected one of {sorted(outputs)})"
            )
        return
    raise ValueError(f"unknown operand '{operand}'")


# ---------------------------------------------------------------------------
# Hashes


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def strategy_hash(config: RunConfig) -> str:
    return sha256_json(config.canonical_strategy())


def run_hash(config: RunConfig, data_version: str, code_version: str) -> str:
    payload = {
        "config": config.canonical(),
        "data_version": data_version,
        "code_version": code_version,
    }
    return sha256_json(payload)


def split_hash(config: RunConfig) -> str | None:
    if config.evaluation.split is None:
        return None
    return sha256_json(config.evaluation.split.model_dump(mode="json"))


def universe_key(config: RunConfig) -> str:
    return config.strategy.instrument_id
