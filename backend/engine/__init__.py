"""Nautilus adapter: instruments, execution profiles, RuleStrategy, runner.

Only this package imports ``nautilus_trader`` (AGENTS.md §3).
"""

from backend.engine.execution import (
    PROFILE_TABLE,
    BookSnapshot,
    BookSnapshotProvider,
    EffectiveExecution,
    NoBookProvider,
    ProfiledFeeModel,
    ProfiledFillModel,
    calculate_cost_estimate,
    resolve_profile,
)
from backend.engine.instruments import (
    build_binary_option,
    build_pair,
    instrument_id_for,
    pair_ids_for,
    validate_settlement_value,
)
from backend.engine.rule_strategy import (
    OrderIntent,
    RuleStrategy,
    RuleStrategyConfig,
    compute_sizing,
    evaluate_condition,
)

__all__ = [
    "PROFILE_TABLE",
    "BookSnapshot",
    "BookSnapshotProvider",
    "EffectiveExecution",
    "NoBookProvider",
    "OrderIntent",
    "ProfiledFeeModel",
    "ProfiledFillModel",
    "RuleStrategy",
    "RuleStrategyConfig",
    "build_binary_option",
    "build_pair",
    "calculate_cost_estimate",
    "compute_sizing",
    "evaluate_condition",
    "instrument_id_for",
    "pair_ids_for",
    "resolve_profile",
    "validate_settlement_value",
]
