import { describe, expect, it } from "vitest";
import type { Bar, IndicatorPreviewResponse, IndicatorSpec } from "../api/types";
import { buildChartIndicators } from "./chartIndicators";

const bars: Bar[] = [
  { ts_event: "2024-10-19T10:00:00Z", open: 0.4, high: 0.42, low: 0.39, close: 0.41 },
  { ts_event: "2024-10-19T10:01:00Z", open: 0.41, high: 0.43, low: 0.4, close: 0.42 },
  { ts_event: "2024-10-19T10:02:00Z", open: 0.42, high: 0.44, low: 0.41, close: 0.43 },
];

const specs: IndicatorSpec[] = [
  { id: "ema", label: "EMA", pane: "overlay", parameters: [], outputs: ["value"], warmup: 2 },
  {
    id: "rsi",
    label: "RSI",
    pane: "separate",
    parameters: [],
    outputs: [{ key: "value" }],
    warmup: 1,
  },
];

const preview: IndicatorPreviewResponse = {
  series: {
    ema: { value: [null, null, 0.42] },
    rsi: { value: [null, 55.5, 60.1] },
  },
  warmup: { ema: 2, rsi: 1 },
  bars: 3,
};

describe("chart indicator series", () => {
  it("omits warm-up nulls instead of plotting them as zero", () => {
    const { series, mismatch } = buildChartIndicators({
      bars,
      instances: [
        { instance_id: "ema", type: "ema", params: {}, visible: true },
      ],
      preview,
      specs,
    });
    expect(mismatch).toBeNull();
    expect(series).toHaveLength(1);
    expect(series[0].lines[0].points).toHaveLength(1);
    expect(series[0].lines[0].points[0].value).toBe(0.42);
    expect(series[0].lines[0].points.some((point) => point.value === 0)).toBe(false);
    expect(series[0].warmup).toBe(2);
  });

  it("keeps separate-pane indicators distinct and honours visibility", () => {
    const { series } = buildChartIndicators({
      bars,
      instances: [
        { instance_id: "ema", type: "ema", params: {}, visible: true },
        { instance_id: "rsi", type: "rsi", params: {}, visible: true },
        { instance_id: "hidden", type: "ema", params: {}, visible: false },
      ],
      preview: { ...preview, series: { ...preview.series, hidden: { value: [1, 2, 3] } } },
      specs,
    });
    expect(series.map((item) => item.pane)).toEqual(["overlay", "separate"]);
    expect(series.map((item) => item.id)).not.toContain("hidden");
  });

  it("surfaces a series that the server did not return", () => {
    const { series, mismatch } = buildChartIndicators({
      bars,
      instances: [{ instance_id: "vwap", type: "vwap", params: {}, visible: true }],
      preview,
      specs,
    });
    expect(series).toEqual([]);
    expect(mismatch).toContain("no series returned for vwap");
  });
});
