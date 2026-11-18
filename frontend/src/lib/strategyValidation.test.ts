import { describe, expect, it } from "vitest";
import type { IndicatorSpec } from "../api/types";
import { validateBacktestConfig, validateConfigJson } from "../lib/strategyValidation";

const specs: IndicatorSpec[] = [
  {
    id: "ema",
    label: "EMA",
    pane: "overlay",
    parameters: [{ name: "period", type: "int", min: 2, max: 400, default: 20, step: 1 }],
    outputs: [{ key: "value" }],
    warmup: 19,
  },
  {
    id: "rsi",
    label: "RSI",
    pane: "separate",
    parameters: [{ name: "period", type: "int", min: 2, max: 200, default: 14, step: 1 }],
    outputs: [{ key: "value" }],
    warmup: 14,
  },
];

function validConfig() {
  return {
    strategy: {
      instrument_id: "x-YES.POLYMARKET",
      bar_type: "1m",
      indicators: [{ instance_id: "ema20", type: "ema", params: { period: 20 } }],
      long_entry: {
        all: [
          { crosses_above: ["close", "indicator:ema20:value"] },
          { gt: ["indicator:ema20:value", 0.5] },
        ],
      },
      long_exit: { crosses_below: ["close", "indicator:ema20:value"] },
      settlement: "hold_to_expiry",
    },
    execution: {
      profile: "conservative",
      fee_bps: 0,
      slippage_bps: 10,
      min_abs_slippage: 0.001,
    },
    risk: { sizing: { mode: "fixed_stake", value: 100 }, max_trades: 10 },
    evaluation: { date_range: { from: "2024-01-01", to: "2024-02-01" } },
  };
}

describe("strategy validation", () => {
  it("accepts a valid declarative rule tree", () => {
    const result = validateBacktestConfig(validConfig(), specs);
    expect(result.issues).toEqual([]);
    expect(result.ok).toBe(true);
  });

  it("rejects an unknown indicator type", () => {
    const config = validConfig();
    config.strategy.indicators[0] = { instance_id: "nope", type: "magic", params: {} } as never;
    const result = validateBacktestConfig(config, specs);
    expect(result.ok).toBe(false);
    expect(result.issues).toContainEqual(
      expect.objectContaining({ path: "strategy.indicators[0].type" }),
    );
  });

  it("rejects out-of-range and unknown parameters", () => {
    const config = validConfig();
    config.strategy.indicators[0].params = { period: 1, nonsense: 2 } as never;
    const result = validateBacktestConfig(config, specs);
    expect(result.ok).toBe(false);
    expect(result.issues.some((issue) => issue.message.includes("at least 2"))).toBe(true);
    expect(result.issues.some((issue) => issue.message.includes('unknown parameter "nonsense"'))).toBe(
      true,
    );
  });

  it("rejects an empty tree and an empty all[] node", () => {
    const empty = validConfig();
    empty.strategy.long_entry = {} as never;
    expect(validateBacktestConfig(empty, specs).issues).toContainEqual(
      expect.objectContaining({ message: "empty rule tree" }),
    );

    const emptyAll = validConfig();
    emptyAll.strategy.long_entry = { all: [] } as never;
    expect(validateBacktestConfig(emptyAll, specs).issues).toContainEqual(
      expect.objectContaining({ message: "all requires at least one condition" }),
    );
  });

  it("rejects malformed indicator operands", () => {
    const config = validConfig();
    config.strategy.long_entry = { gt: ["indicator:ema20", 0.5] } as never;
    const result = validateBacktestConfig(config, specs);
    expect(result.issues.some((issue) => issue.message.includes("indicator:<instance_id>"))).toBe(
      true,
    );

    const unknown = validConfig();
    unknown.strategy.long_entry = { gt: ["indicator:sma99:value", 0.5] } as never;
    expect(
      validateBacktestConfig(unknown, specs).issues.some((issue) =>
        issue.message.includes('unknown indicator instance "sma99"'),
      ),
    ).toBe(true);
  });

  it("rejects an unknown output and an unknown operator", () => {
    const output = validConfig();
    output.strategy.long_entry = { gt: ["indicator:ema20:histogram", 0.5] } as never;
    expect(
      validateBacktestConfig(output, specs).issues.some((issue) =>
        issue.message.includes('unknown output "histogram"'),
      ),
    ).toBe(true);

    const operator = validConfig();
    operator.strategy.long_entry = { between: ["close", 0.4, 0.6] } as never;
    expect(
      validateBacktestConfig(operator, specs).issues.some((issue) =>
        issue.message.includes('unknown rule operator "between"'),
      ),
    ).toBe(true);
  });

  it("caps rule depth", () => {
    const config = validConfig();
    let node: Record<string, unknown> = { gt: ["close", 0.5] };
    for (let index = 0; index < 8; index += 1) node = { not: node };
    config.strategy.long_entry = node as never;
    const result = validateBacktestConfig(config, specs);
    expect(result.issues.some((issue) => issue.message.includes("maximum depth"))).toBe(true);
  });

  it("requires at least one entry rule unless a built-in is selected", () => {
    const config = validConfig();
    delete (config.strategy as { long_entry?: unknown }).long_entry;
    delete (config.strategy as { long_exit?: unknown }).long_exit;
    expect(
      validateBacktestConfig(config, specs).issues.some((issue) =>
        issue.message.includes("at least one entry rule"),
      ),
    ).toBe(true);

    (config.strategy as { builtin_id?: string }).builtin_id = "sma_trend";
    expect(validateBacktestConfig(config, specs).ok).toBe(true);
  });

  it("validates execution and sizing sections", () => {
    const config = validConfig();
    config.execution.profile = "turbo" as never;
    config.risk.sizing.value = 0 as never;
    config.risk.max_trades = 0 as never;
    const result = validateBacktestConfig(config, specs);
    expect(result.issues.some((issue) => issue.path === "execution.profile")).toBe(true);
    expect(result.issues.some((issue) => issue.path === "risk.sizing.value")).toBe(true);
    expect(result.issues.some((issue) => issue.path === "risk.max_trades")).toBe(true);
  });

  it("rejects missing sections and JSON parse errors with a path", () => {
    expect(validateBacktestConfig({}, specs).issues[0].path).toBe("strategy");
    const parsed = validateConfigJson("{not json", specs);
    expect(parsed.ok).toBe(false);
    expect(parsed.issues[0].path).toBe("$");
    expect(parsed.issues[0].message).toMatch(/JSON parse error/);
  });
});
