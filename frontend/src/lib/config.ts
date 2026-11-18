import type {
  BacktestConfig,
  EvaluationSection,
  ExecutionProfile,
  ExecutionSection,
  IndicatorInstance,
  RiskSection,
  StrategySection,
} from "../api/types";
import { defaultExecution } from "./execution";

export interface SizingState {
  mode: "fixed_stake" | "risk_pct";
  value: number;
  bankroll: number;
}

export interface BuildConfigArgs {
  instrumentId: string;
  barType: string;
  indicators: IndicatorInstance[];
  authoring: "quick" | "advanced";
  builtinId: string;
  builtinParams: Record<string, number | boolean | string>;
  direction: "long" | "short" | "both";
  advancedConfig: unknown | null;
  profile: ExecutionProfile;
  feeBps: number;
  slippageBps?: number;
  minAbsSlippage?: number;
  sizing: SizingState;
  maxTrades: number;
  from: string;
  to: string;
  split?: EvaluationSection["split"];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function buildBacktestConfig(args: BuildConfigArgs): BacktestConfig {
  const advanced = asRecord(args.advancedConfig);
  const advancedStrategy = asRecord(advanced?.strategy) ?? {};
  const advancedExecution = asRecord(advanced?.execution) ?? {};
  const advancedRisk = asRecord(advanced?.risk) ?? {};
  const advancedEvaluation = asRecord(advanced?.evaluation) ?? {};

  const strategy: StrategySection = {
    ...(advancedStrategy as Partial<StrategySection>),
    instrument_id: args.instrumentId,
    bar_type: args.barType,
    indicators: args.indicators,
    direction: args.direction,
  };

  if (args.authoring === "quick") {
    delete strategy.long_entry;
    delete strategy.long_exit;
    delete strategy.short_entry;
    delete strategy.short_exit;
    strategy.builtin_id = args.builtinId;
    strategy.builtin_params = args.builtinParams;
    strategy.settlement = strategy.settlement ?? "hold_to_expiry";
  }
  // Advanced mode keeps whatever the JSON declares, including a builtin_id
  // with no rule tree; it never strips the authoring path into an empty config.

  const baseExecution = defaultExecution(args.profile, args.feeBps);
  const execution: ExecutionSection = {
    ...(advancedExecution as Partial<ExecutionSection>),
    ...baseExecution,
    profile: args.profile,
    fee_bps: args.profile === "frictionless" ? 0 : args.feeBps,
  };
  if (args.slippageBps !== undefined) execution.slippage_bps = args.slippageBps;
  if (args.minAbsSlippage !== undefined) execution.min_abs_slippage = args.minAbsSlippage;

  const risk: RiskSection = {
    ...(advancedRisk as Partial<RiskSection>),
    sizing: {
      mode: args.sizing.mode,
      value: args.sizing.value,
      ...(args.sizing.mode === "risk_pct" ? { bankroll_gbp: args.sizing.bankroll } : {}),
    },
    max_trades: args.maxTrades,
  };

  const evaluation: EvaluationSection = {
    ...(advancedEvaluation as Partial<EvaluationSection>),
    date_range: {
      ...(asRecord(advancedEvaluation.date_range) ?? {}),
      from: args.from || undefined,
      to: args.to || undefined,
    },
    ...(args.split ? { split: args.split } : {}),
  };

  return { strategy, execution, risk, evaluation };
}

export function prettyConfig(config: unknown): string {
  return JSON.stringify(config, null, 2);
}

export function makeInstanceId(type: string, existing: IndicatorInstance[]): string {
  const base = type.toLowerCase().replace(/[^a-z0-9]+/g, "");
  const used = new Set(existing.map((indicator) => indicator.instance_id));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base}${index}`)) index += 1;
  return `${base}${index}`;
}

/** Default parameters for a newly added indicator, from the registry spec. */
export function defaultParams(
  spec: { parameters?: Array<{ name: string; default: number | boolean }> },
): Record<string, number | boolean> {
  const params: Record<string, number | boolean> = {};
  for (const parameter of spec.parameters ?? []) {
    params[parameter.name] = parameter.default;
  }
  return params;
}
