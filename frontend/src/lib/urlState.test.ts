import { describe, expect, it } from "vitest";
import {
  DEFAULT_WORKSPACE_STATE,
  forgetWorkspaceSettings,
  isDateString,
  loadRememberedDefaults,
  parseWorkspaceSearch,
  rememberWorkspaceSettings,
  serializeWorkspaceState,
  workspaceSearchString,
  type WorkspaceState,
} from "../lib/urlState";

const fullState: WorkspaceState = {
  instrument: "epl-bou-ars-2024-10-19-bou-YES.POLYMARKET",
  league: "EPL",
  barType: "5m",
  from: "2024-10-01",
  to: "2024-12-01",
  indicators: [
    { instance_id: "ema20", type: "ema", params: { period: 20 }, color: "#56B4E9", visible: true },
    { instance_id: "rsi14", type: "rsi", params: { period: 14 }, visible: false },
  ],
  builtin: "sma_trend",
  quickParams: { trend_period: 50, filter_period: 200 },
  quickDirection: "both",
  strategyJson: null,
  profile: "stress",
  runId: "run-123",
  compare: ["run-1", "run-2"],
  phase: "in_play",
  showGoals: false,
};

describe("workspace URL state", () => {
  it("round-trips every shareable field", () => {
    const search = workspaceSearchString(fullState);
    const parsed = parseWorkspaceSearch(search);
    expect(parsed.issues).toEqual([]);
    expect(parsed.state).toEqual(fullState);
  });

  it("parses run, compare, profile, league and the shared-run keys together", () => {
    const parsed = parseWorkspaceSearch(
      "?instrument=x-YES.POLYMARKET&league=EPL&profile=book&run=run_abc&compare=run_1,run_2",
    );
    expect(parsed.issues).toEqual([]);
    expect(parsed.state.instrument).toBe("x-YES.POLYMARKET");
    expect(parsed.state.league).toBe("EPL");
    expect(parsed.state.profile).toBe("book");
    expect(parsed.state.runId).toBe("run_abc");
    expect(parsed.state.compare).toEqual(["run_1", "run_2"]);
    const serialised = serializeWorkspaceState(parsed.state);
    expect(serialised.get("run")).toBe("run_abc");
    expect(serialised.get("compare")).toBe("run_1,run_2");
    expect(serialised.get("profile")).toBe("book");
    expect(serialised.get("league")).toBe("EPL");
  });

  it("round-trips an advanced strategy JSON instead of a builtin id", () => {
    const state: WorkspaceState = {
      ...DEFAULT_WORKSPACE_STATE,
      instrument: "x-YES.POLYMARKET",
      strategyJson: JSON.stringify({
        strategy: {
          instrument_id: "x-YES.POLYMARKET",
          bar_type: "1m",
          long_entry: { gt: ["close", 0.5] },
        },
      }),
      builtin: "sma_trend",
    };
    const parsed = parseWorkspaceSearch(workspaceSearchString(state));
    expect(parsed.issues).toEqual([]);
    expect(parsed.state.strategyJson).toBe(state.strategyJson);
    expect(serializeWorkspaceState(parsed.state).get("builtin")).toBeNull();
  });

  it("falls back to safe defaults and reports unknown enum values", () => {
    const parsed = parseWorkspaceSearch(
      "?bar=7m&profile=turbo&phase=halftime&from=2024-02-31&to=2025-01-01&compare=a,b,c,d",
    );
    expect(parsed.state.barType).toBe("1m");
    expect(parsed.state.profile).toBe("conservative");
    expect(parsed.state.phase).toBe("all");
    expect(parsed.state.from).toBe("");
    expect(parsed.state.to).toBe("2025-01-01");
    expect(parsed.state.compare).toEqual(["a", "b", "c"]);
    expect(parsed.issues.join(" ")).toMatch(/unknown bar type/);
    expect(parsed.issues.join(" ")).toMatch(/unknown execution profile/);
    expect(parsed.issues.join(" ")).toMatch(/invalid from date/);
  });

  it("drops malformed indicator entries and duplicate instance ids", () => {
    const ind = JSON.stringify([
      { instance_id: "ema20", type: "ema", params: { period: 20, bad: { nested: 1 } } },
      { instance_id: "ema20", type: "ema", params: { period: 21 } },
      { type: "rsi" },
      { instance_id: "rsi14", type: "rsi", params: { period: 14 }, color: "not-a-colour" },
    ]);
    const parsed = parseWorkspaceSearch(`?ind=${encodeURIComponent(ind)}`);
    expect(parsed.state.indicators).toHaveLength(2);
    expect(parsed.state.indicators[0].params).toEqual({ period: 20 });
    expect(parsed.state.indicators[1].instance_id).toBe("rsi14");
    expect(parsed.state.indicators[1].color).toBeUndefined();
  });

  it("rejects non-object indicator JSON and bad strategy JSON without throwing", () => {
    const parsed = parseWorkspaceSearch(
      `?ind=${encodeURIComponent("[1,2,3]")}&strategy=${encodeURIComponent("{not json")}`,
    );
    expect(parsed.state.indicators).toEqual([]);
    expect(parsed.state.strategyJson).toBeNull();
    expect(parsed.issues.length).toBeGreaterThan(0);
  });

  it("swaps an inverted date range rather than keeping it", () => {
    const parsed = parseWorkspaceSearch("?from=2025-01-01&to=2024-01-01");
    expect(parsed.state.from).toBe("");
    expect(parsed.state.to).toBe("");
    expect(parsed.issues).toContain("from is after to");
  });

  it("validates strict ISO dates", () => {
    expect(isDateString("2024-02-29")).toBe(true);
    expect(isDateString("2023-02-29")).toBe(false);
    expect(isDateString("2024-13-01")).toBe(false);
    expect(isDateString("01/01/2024")).toBe(false);
  });

  it("remembers settings but never remembers a run id or comparison", () => {
    rememberWorkspaceSettings(fullState);
    const remembered = loadRememberedDefaults();
    expect(remembered.instrument).toBe(fullState.instrument);
    expect(remembered.league).toBe(fullState.league);
    expect(remembered.indicators).toEqual(fullState.indicators);
    expect(remembered.runId).toBe("");
    expect(remembered.compare).toEqual([]);
    forgetWorkspaceSettings();
    expect(loadRememberedDefaults().instrument).toBe("");
  });

  it("serialises a default state to an empty query", () => {
    expect(workspaceSearchString(DEFAULT_WORKSPACE_STATE)).toBe("");
  });

  it("defaults goals on and round-trips goals=0 without serialising goals=1", () => {
    expect(DEFAULT_WORKSPACE_STATE.showGoals).toBe(true);
    const disabled = parseWorkspaceSearch("?goals=0");
    expect(disabled.state.showGoals).toBe(false);
    expect(serializeWorkspaceState(disabled.state).get("goals")).toBe("0");
    expect(parseWorkspaceSearch(workspaceSearchString(disabled.state)).state.showGoals).toBe(false);

    const enabled = parseWorkspaceSearch("?goals=1");
    expect(enabled.state.showGoals).toBe(true);
    expect(serializeWorkspaceState(enabled.state).get("goals")).toBeNull();
  });

  it("falls back to goals on for an invalid goals value without throwing", () => {
    const parsed = parseWorkspaceSearch("?goals=maybe");
    expect(parsed.state.showGoals).toBe(true);
  });
});
