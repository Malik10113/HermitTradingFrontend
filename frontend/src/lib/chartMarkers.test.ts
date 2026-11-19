import { describe, expect, it } from "vitest";
import type { Bar, LifecycleEvent, LifecycleMeta, Trade } from "../api/types";
import {
  alignIndicatorSeries,
  barsToCandleData,
  CHART_COLORS,
  lifecycleEventMarkers,
  lifecycleMarkers,
  normaliseEquityCurves,
  openPositionBands,
  phaseAt,
  phaseWindows,
  positionBands,
  runChartMarkers,
  signalMarkers,
  tradeMarkers,
} from "../lib/chartMarkers";

function bar(ts: string, close = 0.5, volume = 100): Bar {
  return {
    ts_event: ts,
    open: close,
    high: close + 0.01,
    low: close - 0.01,
    close,
    volume_usd: volume,
    trade_count: 2,
  };
}

const lifecycle: LifecycleMeta = {
  actual_kickoff_utc: "2024-10-19T10:00:00Z",
  actual_end_utc: "2024-10-19T12:00:00Z",
  settlement_ts: "2024-10-19T13:00:00Z",
  source: "espn_keyevents",
  precision: "observed",
};

const bars = [
  bar("2024-10-19T09:00:00Z"),
  bar("2024-10-19T10:00:00Z"),
  bar("2024-10-19T11:00:00Z"),
  bar("2024-10-19T12:00:00Z"),
  bar("2024-10-19T13:00:00Z"),
  bar("2024-10-19T14:00:00Z"),
];

function trade(overrides: Partial<Trade> = {}): Trade {
  return {
    trade_id: "t1",
    side: "BUY",
    token: "YES",
    entry_ts: "2024-10-19T10:05:00Z",
    exit_ts: "2024-10-19T11:05:00Z",
    entry_price: 0.42,
    exit_price: 0.48,
    net_pnl_usd: 6,
    ...overrides,
  };
}

describe("candle data mapping", () => {
  it("sorts, de-duplicates and rejects malformed bars", () => {
    const result = barsToCandleData([
      bar("2024-10-19T11:00:00Z", 0.5),
      bar("2024-10-19T10:00:00Z", 0.4),
      bar("2024-10-19T11:00:00Z", 0.55),
      { ts_event: "broken", open: 1, high: 1, low: 1, close: 1 },
    ]);
    expect(result.data.map((point) => point.time)).toEqual([
      Math.floor(Date.parse("2024-10-19T10:00:00Z") / 1000),
      Math.floor(Date.parse("2024-10-19T11:00:00Z") / 1000),
    ]);
    expect(result.data[1].close).toBe(0.55);
    expect(result.invalidCount).toBe(1);
    expect(result.duplicateCount).toBe(1);
  });
});

describe("trade and lifecycle markers", () => {
  it("maps a long entry below the bar and its sell exit above", () => {
    const markers = tradeMarkers([
      trade({ close_side: "SELL", side: "BUY", token: "YES" }),
    ]);
    expect(markers).toHaveLength(2);
    expect(markers[0]).toMatchObject({
      position: "belowBar",
      shape: "arrowUp",
      text: "BUY YES",
      kind: "entry",
      color: CHART_COLORS.up,
    });
    expect(markers[1]).toMatchObject({
      position: "aboveBar",
      shape: "arrowDown",
      text: "SELL YES",
      kind: "exit",
      color: CHART_COLORS.down,
    });
  });

  it("marks a short entry as a BUY of the NO token, never an exit for YES", () => {
    const markers = tradeMarkers([
      trade({ token: "NO", intent: "open_short", close_side: "SELL" }),
    ]);
    expect(markers[0].text).toBe("BUY NO");
    expect(markers[1].text).toBe("SELL NO");
  });

  it("maps a held-to-settlement exit to a settlement circle", () => {
    const markers = tradeMarkers([
      trade({ held_to_settlement: true, exit_ts: "2024-10-19T13:00:00Z" }),
    ]);
    expect(markers[1]).toMatchObject({
      kind: "settlement",
      shape: "circle",
      text: "SETTLE YES",
      color: CHART_COLORS.settlement,
    });
  });

  it("sorts markers by time and carries the trade id for the inspector", () => {
    const markers = tradeMarkers([
      trade({ trade_id: "b", entry_ts: "2024-10-19T12:00:00Z", exit_ts: "2024-10-19T13:00:00Z" }),
      trade({ trade_id: "a", entry_ts: "2024-10-19T10:00:00Z", exit_ts: "2024-10-19T11:00:00Z" }),
    ]);
    expect(markers.map((marker) => marker.tradeId)).toEqual(["a", "a", "b", "b"]);
    expect(markers[0].time).toBeLessThan(markers[2].time);
  });
});

describe("chart markers from run artifacts", () => {
  it("emits kickoff and end markers only when both wallclocks are observed", () => {
    expect(lifecycleMarkers(null)).toEqual([]);
    expect(lifecycleMarkers({ actual_kickoff_utc: "2024-10-19T10:00:00Z" })).toEqual([]);
    expect(lifecycleMarkers({ actual_end_utc: "2024-10-19T12:00:00Z" })).toEqual([]);
    const markers = lifecycleMarkers(lifecycle);
    expect(markers.map((marker) => marker.text)).toEqual(["KICKOFF", "END"]);
    expect(markers.map((marker) => marker.time)).toEqual([
      Math.floor(Date.parse("2024-10-19T10:00:00Z") / 1000),
      Math.floor(Date.parse("2024-10-19T12:00:00Z") / 1000),
    ]);
  });

  it("keeps trade and signal markers together instead of choosing one artifact", () => {
    const markers = runChartMarkers({
      trades: [trade({ trade_id: "t1", close_side: "SELL" })],
      signals: [{ ts: "2024-10-19T10:30:00Z", kind: "signal", side: "BUY", reason: "long_entry" }],
      lifecycle,
    });
    expect(markers.map((marker) => marker.id)).toEqual([
      "lifecycle:kickoff",
      "t1:entry",
      "signal:0:" + Math.floor(Date.parse("2024-10-19T10:30:00Z") / 1000),
      "t1:exit",
      "lifecycle:end",
    ]);
    expect(markers.filter((marker) => marker.id.startsWith("signal:")).length).toBe(1);
    expect(markers.filter((marker) => marker.kind === "entry")).toHaveLength(1);
    expect(markers.filter((marker) => marker.kind === "exit")).toHaveLength(1);
  });

  it("does not emit lifecycle markers or phase shading for an unknown phase", () => {
    const markers = runChartMarkers({
      trades: [trade()],
      signals: [{ ts: "2024-10-19T10:30:00Z", kind: "fill" }],
      lifecycle: { actual_kickoff_utc: "2024-10-19T10:00:00Z", phase: "unknown" },
    });
    expect(markers.some((marker) => marker.id.startsWith("lifecycle:"))).toBe(false);
    expect(phaseWindows(bars, { actual_kickoff_utc: "2024-10-19T10:00:00Z" })).toEqual([]);
  });

  it("labels fill markers from the signals artifact", () => {
    const markers = signalMarkers([{ ts: "2024-10-19T10:00:00Z", kind: "fill", side: "BUY" }]);
    expect(markers[0]).toMatchObject({ kind: "fill", text: "FILL" });
    expect(markers[0].color).toBe(CHART_COLORS.neutral);
  });

  it("uses a colour-blind-safe up/down pair that differs by more than hue", () => {
    expect(CHART_COLORS.up).not.toBe(CHART_COLORS.down);
    expect(CHART_COLORS.settlement).not.toBe(CHART_COLORS.signal);
  });
});

describe("halftime and goal markers", () => {
  const events: LifecycleEvent[] = [
    {
      ts: "2024-10-19T10:55:00Z",
      type: "goal",
      label: "GOAL",
      team: "Bournemouth",
      detail: "Goal! Bournemouth 1-0 Arsenal",
    },
    { ts: "2024-10-19T11:05:00Z", type: "halftime", label: "HALF TIME", team: null, detail: null },
  ];
  const at = (iso: string) => Math.floor(Date.parse(iso) / 1000);

  it("keeps the halftime marker whether or not goals are shown", () => {
    for (const showGoals of [true, false]) {
      const halftime = lifecycleEventMarkers(events, showGoals).filter(
        (marker) => marker.text === "HALF TIME",
      );
      expect(halftime).toHaveLength(1);
      expect(halftime[0]).toMatchObject({
        time: at("2024-10-19T11:05:00Z"),
        position: "aboveBar",
        shape: "circle",
      });
      expect(halftime[0].id.startsWith("lifecycle:halftime:")).toBe(true);
    }
  });

  it("shows goal markers only when goals are enabled", () => {
    const on = lifecycleEventMarkers(events, true);
    const goals = on.filter((marker) => marker.text === "GOAL");
    expect(goals).toHaveLength(1);
    expect(goals[0]).toMatchObject({
      time: at("2024-10-19T10:55:00Z"),
      position: "belowBar",
      shape: "square",
    });
    expect(goals[0].id.startsWith("lifecycle:goal:")).toBe(true);

    const off = lifecycleEventMarkers(events, false);
    expect(off.filter((marker) => marker.text === "GOAL")).toEqual([]);
    expect(off.map((marker) => marker.text)).toEqual(["HALF TIME"]);
  });

  it("skips events with an unparseable time instead of inventing one", () => {
    const markers = lifecycleEventMarkers(
      [
        { ts: "not-a-time", type: "goal", label: "GOAL", team: null, detail: null },
        { ts: "", type: "halftime", label: "HALF TIME", team: null, detail: null },
        ...events,
      ],
      true,
    );
    expect(markers.map((marker) => marker.text)).toEqual(["GOAL", "HALF TIME"]);
    expect(markers.every((marker) => Number.isFinite(marker.time))).toBe(true);
  });

  it("sorts events by time and leaves kickoff/end markers unchanged", () => {
    const markers = runChartMarkers({
      trades: [],
      signals: [],
      lifecycle: { ...lifecycle, events: [events[1], events[0]] },
      events: [events[1], events[0]],
      showGoals: true,
    });
    expect(markers.map((marker) => marker.text)).toEqual([
      "KICKOFF",
      "GOAL",
      "HALF TIME",
      "END",
    ]);
    const kickoff = markers.find((marker) => marker.id === "lifecycle:kickoff");
    expect(kickoff).toMatchObject({ text: "KICKOFF", shape: "arrowDown", position: "aboveBar" });
    expect(markers.find((marker) => marker.id === "lifecycle:end")?.time).toBe(
      at("2024-10-19T12:00:00Z"),
    );
  });
});

describe("phase shading", () => {
  it("builds pre-match, in-play, post-match and settled windows from observed timestamps", () => {
    const windows = phaseWindows(bars, lifecycle);
    expect(windows.map((window) => window.phase)).toEqual([
      "pre_match",
      "in_play",
      "post_match",
      "settled",
    ]);
    expect(windows[0].from).toBe(Math.floor(Date.parse("2024-10-19T09:00:00Z") / 1000));
    expect(windows[1].to).toBe(Math.floor(Date.parse("2024-10-19T12:00:00Z") / 1000));
    expect(windows[2].to).toBe(Math.floor(Date.parse("2024-10-19T13:00:00Z") / 1000));
  });

  it("returns no shading when the lifecycle row is missing", () => {
    expect(phaseWindows(bars, null)).toEqual([]);
    expect(phaseWindows(bars, { actual_kickoff_utc: "2024-10-19T10:00:00Z" })).toEqual([]);
    expect(phaseAt("2024-10-19T10:30:00Z", null)).toBe("unknown");
  });

  it("reads phases in order from observed kickoff and end", () => {
    expect(phaseAt("2024-10-19T09:30:00Z", lifecycle)).toBe("pre_match");
    expect(phaseAt("2024-10-19T10:30:00Z", lifecycle)).toBe("in_play");
    expect(phaseAt("2024-10-19T12:30:00Z", lifecycle)).toBe("post_match");
    expect(phaseAt("2024-10-19T13:30:00Z", lifecycle)).toBe("settled");
  });

  it("maps position windows with distinct YES and NO colours", () => {
    const windows = positionBands([
      trade({ trade_id: "yes", token: "YES" }),
      trade({ trade_id: "no", token: "NO" }),
    ]);
    expect(windows[0].color).toBe(CHART_COLORS.positionLong);
    expect(windows[1].color).toBe(CHART_COLORS.positionShort);
    expect(openPositionBands([{ entry_ts: "2024-10-19T10:00:00Z", exit_ts: null, token: "NO" }])[0].color).toBe(
      CHART_COLORS.positionShort,
    );
  });

  it("maps API position windows to long and short bands", () => {
    const bands = openPositionBands([
      {
        position_id: "P-0001",
        entry_ts: "2024-10-19T10:00:00Z",
        exit_ts: "2024-10-19T10:05:00Z",
        token: "YES",
        side: "long",
      },
      {
        position_id: "P-0002",
        entry_ts: "2024-10-19T10:05:00Z",
        exit_ts: "2024-10-19T10:10:00Z",
        token: "NO",
        side: "short",
      },
    ]);
    expect(bands).toHaveLength(2);
    expect(bands[0]).toMatchObject({
      from: Math.floor(Date.parse("2024-10-19T10:00:00Z") / 1000),
      to: Math.floor(Date.parse("2024-10-19T10:05:00Z") / 1000),
      color: CHART_COLORS.positionLong,
      token: "YES",
    });
    expect(bands[1].color).toBe(CHART_COLORS.positionShort);
  });

  it("returns no shading for absent or empty position windows", () => {
    expect(openPositionBands(null)).toEqual([]);
    expect(openPositionBands(undefined)).toEqual([]);
    expect(openPositionBands([])).toEqual([]);
  });
});

describe("server indicator alignment", () => {
  it("aligns by index and keeps warm-up nulls out", () => {
    const aligned = alignIndicatorSeries(bars, [null, 0.4, 0.45, null, 0.5, 0.55]);
    expect(aligned.mismatch).toBeNull();
    expect(aligned.points).toHaveLength(4);
    expect(aligned.points[0]).toEqual({
      time: Math.floor(Date.parse("2024-10-19T10:00:00Z") / 1000),
      value: 0.4,
    });
  });

  it("aligns by server timestamps when supplied", () => {
    const aligned = alignIndicatorSeries(
      bars,
      [0.4, 0.5],
      ["2024-10-19T10:00:00Z", "2024-10-19T12:00:00Z"],
    );
    expect(aligned.points).toHaveLength(2);
    expect(aligned.points[1].value).toBe(0.5);
  });

  it("surfaces a length mismatch instead of padding values", () => {
    const aligned = alignIndicatorSeries(bars, [0.1, 0.2]);
    expect(aligned.points).toEqual([]);
    expect(aligned.mismatch).toContain("2 values for 6 bars");
  });

  it("reports a timestamp mismatch rather than guessing", () => {
    const aligned = alignIndicatorSeries(bars, [0.4, 0.5], ["2024-10-19T10:00:00Z"]);
    expect(aligned.mismatch).toContain("2 values for 1 timestamps");
  });
});

describe("compare normalisation", () => {
  it("indexes two curves to 100 at their common start", () => {
    const series = normaliseEquityCurves([
      {
        label: "a",
        equity: [
          { ts: "2024-10-19T09:00:00Z", equity_usd: 1000 },
          { ts: "2024-10-19T10:00:00Z", equity_usd: 1100 },
          { ts: "2024-10-19T11:00:00Z", equity_usd: 1200 },
        ],
      },
      {
        label: "b",
        equity: [
          { ts: "2024-10-19T10:00:00Z", equity_usd: 500 },
          { ts: "2024-10-19T11:00:00Z", equity_usd: 450 },
        ],
      },
    ]);
    expect(series).toHaveLength(2);
    const first = series[0];
    expect(first.points[0].value).toBeCloseTo(100);
    expect(first.points[1].value).toBeCloseTo((1200 / 1100) * 100);
    expect(series[1].points[0].value).toBeCloseTo(100);
    expect(series[1].points[1].value).toBeCloseTo(90);
  });

  it("skips runs without two observations", () => {
    const series = normaliseEquityCurves([
      { label: "one", equity: [{ ts: "2024-10-19T09:00:00Z", equity_usd: 100 }] },
    ]);
    expect(series).toEqual([]);
  });
});
