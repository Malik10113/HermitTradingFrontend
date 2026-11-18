import { describe, expect, it } from "vitest";

import type { Preset } from "../api/types";
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

describe("presetWorkspaceSearch", () => {
  it("restores the instrument, range, quick parameters and direction", () => {
    const search = presetWorkspaceSearch(preset);
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

  it("keeps an explicit rule tree instead of reducing it to the builtin", () => {
    const advanced: Preset = {
      ...preset,
      config: {
        ...preset.config,
        strategy: {
          ...preset.config.strategy,
          long_entry: { crosses_above: ["indicator:ema:value", "close"] },
        },
      },
    };
    const { state } = parseWorkspaceSearch(presetWorkspaceSearch(advanced));
    expect(state.strategyJson).toContain("long_entry");
  });
});
