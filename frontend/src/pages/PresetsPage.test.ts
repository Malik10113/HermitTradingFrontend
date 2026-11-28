import { describe, expect, it } from "vitest";

import type { BacktestConfig, Preset } from "../api/types";
import { parseWorkspaceSearch } from "../lib/urlState";
import { presetWorkspaceSearch } from "./PresetsPage";

const preset: Preset = {
  id: "pre_1",
  name: "EMA quick",
  config: {
    strategy: {
      instrument_id: "m-YES.POLYMARKET",
      bar_type: "5m",
      builtin_id: "nautilus_ema_cross",
      builtin_params: { fast_period: 8, slow_period: 21 },
      direction: "short",
      indicators: [],
    },
    execution: { profile: "book", fee_bps: 2 },
    risk: { sizing: { mode: "risk_pct", value: 2, bankroll_usd: 2000 }, max_trades: 25 },
    evaluation: { date_range: { from: "2026-09-18", to: "2026-09-19" } },
  },
};

/** The rail's remembered defaults: nothing here needs extra URL state. */
const quickPreset: Preset = {
  id: "pre_quick",
  name: "EMA quick defaults",
  config: {
    strategy: {
      instrument_id: "m-YES.POLYMARKET",
      bar_type: "5m",
      builtin_id: "nautilus_ema_cross",
      builtin_params: { fast_period: 8, slow_period: 21 },
      direction: "short",
      indicators: [],
    },
    execution: { profile: "book" },
    risk: { sizing: { mode: "fixed_stake", value: 100 }, max_trades: 100 },
    evaluation: { date_range: { from: "2026-09-18", to: "2026-09-19" } },
  },
};

describe("presetWorkspaceSearch", () => {
  it("restores a default-cost preset in quick mode", () => {
    const search = presetWorkspaceSearch(quickPreset);
    const { state, issues } = parseWorkspaceSearch(search);

    expect(issues).toEqual([]);
    expect(state.instrument).toBe("m-YES.POLYMARKET");
    expect(state.barType).toBe("5m");
    expect(state.from).toBe("2026-09-18");
    expect(state.to).toBe("2026-09-19");
    expect(state.profile).toBe("book");
    expect(state.strategyJson).toBeNull();
    expect(state.quickParams).toEqual({ fast_period: 8, slow_period: 21 });
    expect(state.quickDirection).toBe("short");
  });

  it("carries non-default risk and execution instead of dropping them", () => {
    const { state } = parseWorkspaceSearch(presetWorkspaceSearch(preset));
    expect(state.strategyJson).not.toBeNull();
    const restored = JSON.parse(state.strategyJson as string) as BacktestConfig;

    expect(restored.risk).toEqual({
      sizing: { mode: "risk_pct", value: 2, bankroll_usd: 2000 },
      max_trades: 25,
    });
    expect(restored.execution?.fee_bps).toBe(2);
    expect(state.profile).toBe("book");
    expect(state.instrument).toBe("m-YES.POLYMARKET");
    expect(state.from).toBe("2026-09-18");
    expect(state.to).toBe("2026-09-19");
  });

  it("preserves an evaluation split that a quick-mode URL cannot express", () => {
    const splitPreset: Preset = {
      ...quickPreset,
      config: {
        ...quickPreset.config,
        evaluation: {
          date_range: { from: "2026-09-18", to: "2026-09-19" },
          split: { train_end: "2026-09-18", test_start: "2026-09-19" },
        },
      },
    };
    const { state } = parseWorkspaceSearch(presetWorkspaceSearch(splitPreset));
    expect(state.strategyJson).not.toBeNull();
    const restored = JSON.parse(state.strategyJson as string) as BacktestConfig;
    expect(restored.evaluation.split).toEqual({
      train_end: "2026-09-18",
      test_start: "2026-09-19",
    });
  });

  it("keeps an explicit rule tree instead of reducing it to the builtin", () => {
    const advanced: Preset = {
      ...quickPreset,
      config: {
        ...quickPreset.config,
        strategy: {
          ...quickPreset.config.strategy,
          long_entry: { crosses_above: ["indicator:ema:value", "close"] },
        },
      },
    };
    const { state } = parseWorkspaceSearch(presetWorkspaceSearch(advanced));
    expect(state.strategyJson).toContain("long_entry");
  });
});
