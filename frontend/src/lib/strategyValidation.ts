import type {
  BacktestConfig,
  IndicatorInstance,
  IndicatorSpec,
  RuleNode,
} from "../api/types";
import { EXECUTION_PROFILES } from "./barTypes";

export interface ValidationIssue {
  path: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  issues: ValidationIssue[];
  value?: unknown;
}

export const MAX_RULE_DEPTH = 6;
export const MAX_RULE_NODES = 64;

const COMPARATORS = new Set(["gt", "gte", "lt", "lte", "crosses_above", "crosses_below"]);
const BOOLEAN_KEYS = new Set(["all", "any", "not"]);
const PRICE_FIELDS = new Set([
  "open",
  "high",
  "low",
  "close",
  "volume",
  "volume_usd",
  "trade_count",
]);
const LIFECYCLE_FIELDS = new Set([
  "time_to_kickoff_minutes",
  "time_since_kickoff_minutes",
  "time_to_settlement_minutes",
  "phase",
  "market_status",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function outputKeys(spec: IndicatorSpec): string[] {
  const outputs = spec.outputs ?? ["value"];
  return outputs.map((output) => (typeof output === "string" ? output : output.key));
}

function parameterIssues(
  indicator: Record<string, unknown>,
  spec: IndicatorSpec,
  path: string,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const params = indicator.params;
  if (params !== undefined && !isRecord(params)) {
    return [{ path: `${path}.params`, message: "params must be an object" }];
  }
  if (!isRecord(params)) return issues;

  const definitions = spec.parameters ?? [];
  const known = new Set(definitions.map((parameter) => parameter.name));
  for (const key of Object.keys(params)) {
    if (definitions.length > 0 && !known.has(key)) {
      issues.push({ path: `${path}.params.${key}`, message: `unknown parameter "${key}"` });
    }
  }
  for (const definition of definitions) {
    if (!(definition.name in params)) continue;
    const value = params[definition.name];
    const valuePath = `${path}.params.${definition.name}`;
    const type = definition.type;
    if (type === "bool" || type === "boolean") {
      if (typeof value !== "boolean") {
        issues.push({ path: valuePath, message: "expected a boolean" });
      }
      continue;
    }
    if (typeof value !== "number" || !Number.isFinite(value)) {
      issues.push({ path: valuePath, message: "expected a number" });
      continue;
    }
    if (definition.min !== undefined && definition.min !== null && value < definition.min) {
      issues.push({
        path: valuePath,
        message: `must be at least ${definition.min} (got ${value})`,
      });
    }
    if (definition.max !== undefined && definition.max !== null && value > definition.max) {
      issues.push({
        path: valuePath,
        message: `must be at most ${definition.max} (got ${value})`,
      });
    }
    if ((type === "int" || type === "integer") && !Number.isInteger(value)) {
      issues.push({ path: valuePath, message: "expected an integer" });
    }
  }
  return issues;
}

function operandIssues(
  operand: unknown,
  path: string,
  instances: Set<string>,
): ValidationIssue[] {
  if (typeof operand === "number" || typeof operand === "boolean") return [];
  if (typeof operand !== "string") {
    return [{ path, message: "operand must be a price field, indicator reference or number" }];
  }
  if (PRICE_FIELDS.has(operand) || LIFECYCLE_FIELDS.has(operand)) return [];
  if (operand.startsWith("indicator:")) {
    const parts = operand.split(":");
    if (parts.length !== 3 || !parts[1] || !parts[2]) {
      return [
        {
          path,
          message: 'indicator operand must look like "indicator:<instance_id>:<output>"',
        },
      ];
    }
    if (!instances.has(parts[1])) {
      return [{ path, message: `unknown indicator instance "${parts[1]}"` }];
    }
    return [];
  }
  return [
    {
      path,
      message: `unknown operand "${operand}"`,
    },
  ];
}

function validateRuleNode(
  node: unknown,
  path: string,
  instances: Map<string, { type: string; spec?: IndicatorSpec }>,
  depth: number,
  counter: { nodes: number },
): ValidationIssue[] {
  if (depth > MAX_RULE_DEPTH) {
    return [{ path, message: `rule tree exceeds the maximum depth of ${MAX_RULE_DEPTH}` }];
  }
  counter.nodes += 1;
  if (counter.nodes > MAX_RULE_NODES) {
    return [{ path, message: `rule tree exceeds the maximum of ${MAX_RULE_NODES} nodes` }];
  }
  if (!isRecord(node)) {
    return [{ path, message: "condition must be an object" }];
  }
  const keys = Object.keys(node);
  if (keys.length === 0) {
    return [{ path, message: "empty rule tree" }];
  }
  if (keys.length !== 1) {
    return [{ path, message: "a condition must have exactly one operator" }];
  }
  const operator = keys[0];
  const value = node[operator];

  if (operator === "all" || operator === "any") {
    if (!Array.isArray(value) || value.length === 0) {
      return [{ path: `${path}.${operator}`, message: `${operator} requires at least one condition` }];
    }
    return value.flatMap((child, index) =>
      validateRuleNode(child, `${path}.${operator}[${index}]`, instances, depth + 1, counter),
    );
  }

  if (operator === "not") {
    return validateRuleNode(value, `${path}.not`, instances, depth + 1, counter);
  }

  if (COMPARATORS.has(operator)) {
    if (!Array.isArray(value) || value.length !== 2) {
      return [
        {
          path: `${path}.${operator}`,
          message: `${operator} requires exactly two operands`,
        },
      ];
    }
    const issues: ValidationIssue[] = [];
    value.forEach((operand, index) => {
      issues.push(...operandIssues(operand, `${path}.${operator}[${index}]`, new Set(instances.keys())));
      if (typeof operand === "string" && operand.startsWith("indicator:")) {
        const [, instanceId, output] = operand.split(":");
        const instance = instances.get(instanceId);
        if (instance?.spec && output && !outputKeys(instance.spec).includes(output)) {
          issues.push({
            path: `${path}.${operator}[${index}]`,
            message: `unknown output "${output}" for indicator "${instanceId}"`,
          });
        }
      }
    });
    return issues;
  }

  if (BOOLEAN_KEYS.has(operator)) return [];
  return [{ path: `${path}.${operator}`, message: `unknown rule operator "${operator}"` }];
}

function instanceIssues(
  raw: unknown,
  specsById: Map<string, IndicatorSpec>,
): { issues: ValidationIssue[]; instances: Map<string, { type: string; spec?: IndicatorSpec }> } {
  const issues: ValidationIssue[] = [];
  const instances = new Map<string, { type: string; spec?: IndicatorSpec }>();
  if (raw === undefined) return { issues, instances };
  if (!Array.isArray(raw)) {
    return { issues: [{ path: "strategy.indicators", message: "must be an array" }], instances };
  }
  raw.forEach((item, index) => {
    const path = `strategy.indicators[${index}]`;
    if (!isRecord(item)) {
      issues.push({ path, message: "indicator instance must be an object" });
      return;
    }
    const instanceId = item.instance_id;
    const type = item.type;
    if (typeof instanceId !== "string" || !instanceId.trim()) {
      issues.push({ path: `${path}.instance_id`, message: "instance_id is required" });
      return;
    }
    if (typeof type !== "string" || !type.trim()) {
      issues.push({ path: `${path}.type`, message: "type is required" });
      return;
    }
    const spec = specsById.get(type);
    if (specsById.size > 0 && !spec) {
      issues.push({ path: `${path}.type`, message: `unknown indicator "${type}"` });
      return;
    }
    if (instances.has(instanceId)) {
      issues.push({ path: `${path}.instance_id`, message: `duplicate instance_id "${instanceId}"` });
      return;
    }
    instances.set(instanceId, { type, spec });
    if (spec) issues.push(...parameterIssues(item, spec, `${path}`));
  });
  return { issues, instances };
}

export function validateBacktestConfig(
  raw: unknown,
  specs: IndicatorSpec[],
): ValidationResult {
  const issues: ValidationIssue[] = [];
  if (!isRecord(raw)) {
    return { ok: false, issues: [{ path: "$", message: "configuration must be a JSON object" }] };
  }

  const strategy = raw.strategy;
  if (!isRecord(strategy)) {
    return {
      ok: false,
      issues: [{ path: "strategy", message: "strategy section is required" }],
    };
  }

  if (typeof strategy.instrument_id !== "string" || !strategy.instrument_id.trim()) {
    issues.push({ path: "strategy.instrument_id", message: "an instrument is required" });
  }
  if (typeof strategy.bar_type !== "string" || !strategy.bar_type.trim()) {
    issues.push({ path: "strategy.bar_type", message: "a bar type is required" });
  }

  const specsById = new Map(specs.map((spec) => [spec.id, spec]));
  const { issues: indicatorIssueList, instances } = instanceIssues(strategy.indicators, specsById);
  issues.push(...indicatorIssueList);

  const ruleKeys = ["long_entry", "long_exit", "short_entry", "short_exit"] as const;
  let nonEmptyRules = 0;
  for (const key of ruleKeys) {
    const rule = strategy[key];
    if (rule === undefined || rule === null) continue;
    const counter = { nodes: 0 };
    const ruleIssues = validateRuleNode(rule, `strategy.${key}`, instances, 1, counter);
    issues.push(...ruleIssues);
    if (ruleIssues.length === 0) nonEmptyRules += 1;
  }
  const isBuiltin = typeof strategy.builtin_id === "string" && strategy.builtin_id.length > 0;
  if (!isBuiltin && nonEmptyRules === 0) {
    issues.push({
      path: "strategy",
      message: "add at least one entry rule, or select a built-in strategy",
    });
  }

  const execution = raw.execution;
  if (execution !== undefined) {
    if (!isRecord(execution)) {
      issues.push({ path: "execution", message: "execution section must be an object" });
    } else {
      if (
        typeof execution.profile !== "string" ||
        !(EXECUTION_PROFILES as string[]).includes(execution.profile)
      ) {
        issues.push({ path: "execution.profile", message: "unknown execution profile" });
      }
      for (const key of ["fee_bps", "slippage_bps", "min_abs_slippage"] as const) {
        const value = execution[key];
        if (value !== undefined && (typeof value !== "number" || !Number.isFinite(value) || value < 0)) {
          issues.push({ path: `execution.${key}`, message: "must be a non-negative number" });
        }
      }
    }
  }

  const risk = raw.risk;
  if (risk !== undefined) {
    if (!isRecord(risk)) {
      issues.push({ path: "risk", message: "risk section must be an object" });
    } else {
      const sizing = risk.sizing;
      if (!isRecord(sizing)) {
        issues.push({ path: "risk.sizing", message: "sizing is required" });
      } else if (sizing.mode !== "fixed_stake" && sizing.mode !== "risk_pct") {
        issues.push({ path: "risk.sizing.mode", message: "sizing mode must be fixed_stake or risk_pct" });
      } else if (typeof sizing.value !== "number" || !Number.isFinite(sizing.value) || sizing.value <= 0) {
        issues.push({ path: "risk.sizing.value", message: "sizing value must be greater than zero" });
      }
      if (risk.max_trades !== undefined) {
        const maxTrades = risk.max_trades;
        if (typeof maxTrades !== "number" || !Number.isInteger(maxTrades) || maxTrades < 1) {
          issues.push({ path: "risk.max_trades", message: "max_trades must be a positive integer" });
        }
      }
    }
  }

  return { ok: issues.length === 0, issues, value: raw };
}

export function validateConfigJson(text: string, specs: IndicatorSpec[]): ValidationResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid JSON";
    return { ok: false, issues: [{ path: "$", message: `JSON parse error: ${message}` }] };
  }
  return validateBacktestConfig(parsed, specs);
}

export function aspectConfig(raw: unknown): Partial<BacktestConfig> {
  return isRecord(raw) ? (raw as Partial<BacktestConfig>) : {};
}

export type { IndicatorInstance, RuleNode };
