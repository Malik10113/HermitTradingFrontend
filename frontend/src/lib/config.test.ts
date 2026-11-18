import { describe, expect, it } from "vitest";
import { buildBacktestConfig, type SizingState, type BuildConfigArgs } from "../lib/config";

const sizing: SizingState = { mode: "fixed_stake", value: 100, bankroll: 1000 };

function args(overrides: Partial<BuildConfigArgs> = {}): BuildConfigArgs {
  return {
    instrumentId: "x-YES.POLYMARKET",
    barType: "1m",
    indicators: [],
    authoring: "quick",
    builtinId: "nautilus_ema_cross",
    builtinParams: { fast_period: 12, slow_period: 26 },
    direction: "both",
    advancedConfig: null,
    profile: "conservative",
    feeBps: 0,
    sizing,
    maxTrades: 100,
    from: "2024-10-01",
    to: "2024-11-01",
    ...overrides,
  };
}

describe("backtest config builder", () => {
  it("uses builtin_id and params in quick mode", () => {
    const config = buildBacktestConfig(args());
    expect(config.strategy.builtin_id).toBe("nautilus_ema_cross");
    expect(config.strategy.builtin_params).toEqual({ fast_period: 12, slow_period: 26 });
    expect(config.strategy.long_entry).toBeUndefined();
    expect(config.execution.profile).toBe("conservative");
    expect(config.execution.slippage_bps).toBe(10);
    expect(config.evaluation.date_range).toEqual({ from: "2024-10-01", to: "2024-11-01" });
  });

  it("keeps a builtin_id written in advanced JSON instead of stripping it", () => {
    const config = buildBacktestConfig(
      args({
        authoring: "advanced",
        advancedConfig: {
          strategy: {
            instrument_id: "ignored",
            bar_type: "ignored",
            builtin_id: "sma_trend",
            builtin_params: { trend_period: 50 },
          },
        },
      }),
    );
    expect(config.strategy.builtin_id).toBe("sma_trend");
    expect(config.strategy.builtin_params).toEqual({ trend_period: 50 });
    expect(config.strategy.instrument_id).toBe("x-YES.POLYMARKET");
  });

  it("merges advanced indicators with chart instances without dropping rule references", () => {
    const config = buildBacktestConfig(
      args({
        authoring: "advanced",
        indicators: [{ instance_id: "ema20", type: "ema", params: { period: 20 } }],
        advancedConfig: {
          strategy: {
            instrument_id: "ignored",
            bar_type: "1m",
            indicators: [
              { instance_id: "ema50", type: "ema", params: { period: 50 } },
              { instance_id: "ema20", type: "ema", params: { period: 21 } },
            ],
            long_entry: { crosses_above: ["indicator:ema20:value", "indicator:ema50:value"] },
          },
        },
      }),
    );
    expect(config.strategy.indicators).toEqual([
      { instance_id: "ema50", type: "ema", params: { period: 50 } },
      { instance_id: "ema20", type: "ema", params: { period: 20 } },
    ]);
  });

  it("sends risk-pct sizing with a USD bankroll", () => {
    const config = buildBacktestConfig(
      args({ sizing: { mode: "risk_pct", value: 2, bankroll: 5000 } }),
    );
    expect(config.risk.sizing).toEqual({ mode: "risk_pct", value: 2, bankroll_usd: 5000 });
  });

  it("forces zero fees for the Frictionless profile", () => {
    const config = buildBacktestConfig(args({ profile: "frictionless", feeBps: 25 }));
    expect(config.execution.fee_bps).toBe(0);
    expect(config.execution.slippage_bps).toBe(0);
    expect(config.execution.min_abs_slippage).toBe(0);
  });
});
