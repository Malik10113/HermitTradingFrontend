import { describe, expect, it } from "vitest";
import type { Bar, LifecycleMeta, Trade } from "../api/types";
import {
  alignIndicatorSeries,
  barsToCandleData,
  CHART_COLORS,
  normaliseEquityCurves,
  openPositionBands,
  phaseAt,
  phaseWindows,
  positionBands,
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
