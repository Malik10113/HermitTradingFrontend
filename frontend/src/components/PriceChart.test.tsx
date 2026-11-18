import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Bar, LifecycleMeta, Trade } from "../api/types";
import { PriceChart } from "./PriceChart";
import { ThemeProvider } from "../state/theme";

const chartState = vi.hoisted(() => ({
  markers: [] as Array<{ text?: string; id?: string }>,
  candleTimes: [] as number[],
  volumeTimes: [] as number[],
}));

vi.mock("lightweight-charts", () => {
  const makeSeries = (kind: "candle" | "volume" | "line") => {
    const series = {
      setData: (data: Array<{ time: number }>) => {
        if (kind === "candle") chartState.candleTimes = data.map((point) => point.time);
        if (kind === "volume") chartState.volumeTimes = data.map((point) => point.time);
      },
      setMarkers: (markers: Array<{ text?: string; id?: string }>) => {
        if (kind === "candle") chartState.markers = markers;
      },
      priceScale: () => ({ applyOptions: () => {} }),
    };
    return series;
  };
  const timeScale = {
    fitContent: () => {},
    width: () => 600,
    getVisibleRange: () => null,
    timeToCoordinate: () => null,
    subscribeVisibleLogicalRangeChange: () => {},
    unsubscribeVisibleLogicalRangeChange: () => {},
    setVisibleLogicalRange: () => {},
  };
  const chart = {
    addCandlestickSeries: () => makeSeries("candle"),
    addHistogramSeries: () => makeSeries("volume"),
    addLineSeries: () => makeSeries("line"),
    timeScale: () => timeScale,
    subscribeClick: () => {},
    remove: () => {},
    applyOptions: () => {},
  };
  return {
    createChart: () => chart,
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
  };
});

const bars: Bar[] = [
  {
    ts_event: "2024-10-19T10:00:00Z",
    open: 0.4,
    high: 0.42,
    low: 0.39,
    close: 0.41,
    volume_usd: 120,
    trade_count: 4,
  },
  {
    ts_event: "2024-10-19T10:05:00Z",
    open: 0.41,
    high: 0.43,
    low: 0.4,
    close: 0.42,
    volume_usd: 90,
    trade_count: 3,
  },
  {
    ts_event: "2024-10-19T12:00:00Z",
    open: 0.42,
    high: 0.44,
    low: 0.41,
    close: 0.43,
    volume_usd: 60,
    trade_count: 2,
  },
];

const trade: Trade = {
  trade_id: "T-0001",
  side: "BUY",
  close_side: "SELL",
  token: "YES",
  entry_ts: "2024-10-19T10:00:00Z",
  exit_ts: "2024-10-19T10:05:00Z",
  entry_price: 0.4,
  exit_price: 0.42,
  gross_pnl_usd: 2,
  net_pnl_usd: 1.8,
  fees_usd: 0.2,
};

function renderChart(lifecycle: LifecycleMeta | null) {
  return render(
    <ThemeProvider>
      <PriceChart bars={bars} lifecycle={lifecycle} trades={[trade]} signals={[]} />
    </ThemeProvider>,
  );
}

describe("price chart markers", () => {
  beforeEach(() => {
    chartState.markers = [];
    chartState.candleTimes = [];
    chartState.volumeTimes = [];
  });

  it("renders candles and volume from the supplied bars only", () => {
    renderChart(null);
    expect(chartState.candleTimes).toEqual(
      bars.map((bar) => Math.floor(Date.parse(bar.ts_event) / 1000)),
    );
    expect(chartState.volumeTimes).toEqual(chartState.candleTimes);
  });

  it("shows a clear unknown-phase badge and no lifecycle markers", () => {
    renderChart(null);
    expect(screen.getByText(/Phase unknown/i)).toBeInTheDocument();
    expect(chartState.markers.map((marker) => marker.id)).toEqual([
      "T-0001:entry",
      "T-0001:exit",
    ]);
    expect(chartState.markers.some((marker) => marker.id?.startsWith("lifecycle:"))).toBe(false);
  });

  it("adds kickoff and end markers only once both observed wallclocks exist", () => {
    renderChart({
      actual_kickoff_utc: "2024-10-19T10:00:00Z",
      actual_end_utc: "2024-10-19T12:00:00Z",
    });
    const ids = chartState.markers.map((marker) => marker.id);
    expect(ids).toContain("lifecycle:kickoff");
    expect(ids).toContain("lifecycle:end");
    expect(screen.queryByText(/Phase unknown/i)).toBeNull();
  });
});
