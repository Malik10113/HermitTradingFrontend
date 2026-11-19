import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import type { Bar, LifecycleMeta, Trade } from "../api/types";
import type { ChartIndicatorSeries } from "./PriceChart";
import { PriceChart } from "./PriceChart";
import { ThemeProvider } from "../state/theme";

const chartState = vi.hoisted(() => ({
  markers: [] as Array<{ text?: string; id?: string }>,
  candleTimes: [] as number[],
  volumeTimes: [] as number[],
  lineSeriesOptions: [] as Array<Record<string, unknown>>,
  createChartCount: 0,
  crosshairHandlers: [] as Array<(param: { time?: unknown }) => void>,
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
  const makeChart = () => {
    const timeScale = {
      fitContent: () => {},
      width: () => 600,
      getVisibleRange: () => null,
      timeToCoordinate: () => null,
      subscribeVisibleLogicalRangeChange: () => {},
      unsubscribeVisibleLogicalRangeChange: () => {},
      setVisibleLogicalRange: () => {},
    };
    return {
      addCandlestickSeries: () => makeSeries("candle"),
      addHistogramSeries: () => makeSeries("volume"),
      addLineSeries: (options: Record<string, unknown>) => {
        chartState.lineSeriesOptions.push(options ?? {});
        return makeSeries("line");
      },
      timeScale: () => timeScale,
      subscribeClick: () => {},
      subscribeCrosshairMove: (handler: (param: { time?: unknown }) => void) => {
        chartState.crosshairHandlers.push(handler);
      },
      unsubscribeCrosshairMove: () => {},
      remove: () => {},
      applyOptions: () => {},
    };
  };
  return {
    createChart: () => {
      chartState.createChartCount += 1;
      return makeChart();
    },
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

const seconds = (iso: string) => Math.floor(Date.parse(iso) / 1000);

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

const macd: ChartIndicatorSeries = {
  id: "macd",
  label: "MACD",
  color: "#56B4E9",
  pane: "overlay",
  lines: [
    { key: "value", label: "Value", points: [{ time: seconds(bars[0].ts_event), value: 0.01 }] },
    { key: "signal", label: "Signal", points: [{ time: seconds(bars[0].ts_event), value: 0.02 }] },
    {
      key: "histogram",
      label: "Histogram",
      points: [{ time: seconds(bars[0].ts_event), value: -0.01 }],
    },
  ],
};

const rsi: ChartIndicatorSeries = {
  id: "rsi",
  label: "RSI",
  color: "#E69F00",
  pane: "separate",
  lines: [{ key: "value", label: "Value", points: [{ time: seconds(bars[0].ts_event), value: 55.5 }] }],
};

const macdSeparate: ChartIndicatorSeries = { ...macd, pane: "separate" };

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
    chartState.lineSeriesOptions = [];
    chartState.createChartCount = 0;
    chartState.crosshairHandlers = [];
  });

  it("renders candles and volume from the supplied bars only", () => {
    renderChart(null);
    expect(chartState.candleTimes).toEqual(bars.map((bar) => seconds(bar.ts_event)));
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

  it("renders an empty position-window list without inventing shading or markers", () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={null}
          trades={[]}
          signals={[]}
          positions={[]}
        />
      </ThemeProvider>,
    );
    expect(chartState.markers).toEqual([]);
    expect(screen.getByRole("img", { name: /Price chart with 3 bars/ })).toBeInTheDocument();
  });

  it("shows the hovered bar in the OHLC strip and restores the last bar on leave", () => {
    renderChart(null);
    const legend = screen.getByTestId("price-chart-legend");
    expect(legend.textContent).toContain("Last bar 2024-10-19 12:00 UTC");
    expect(legend.textContent).toContain("Vol $60.00");
    expect(legend.textContent).toContain("Fills 2");

    const handler = chartState.crosshairHandlers[0];
    expect(handler).toBeTypeOf("function");
    act(() => handler({ time: seconds(bars[1].ts_event) }));

    expect(legend.textContent).toContain("Bar 2024-10-19 10:05 UTC");
    expect(legend.textContent).toContain("O 0.410");
    expect(legend.textContent).toContain("H 0.430");
    expect(legend.textContent).toContain("L 0.400");
    expect(legend.textContent).toContain("C 0.420");
    expect(legend.textContent).toContain("Vol $90.00");
    expect(legend.textContent).toContain("Fills 3");

    act(() => handler({ time: undefined }));
    expect(legend.textContent).toContain("Last bar 2024-10-19 12:00 UTC");
    expect(legend.textContent).toContain("Vol $60.00");
  });

  it("gives every MACD output its own title and colour", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[macd]} />
      </ThemeProvider>,
    );
    const options = chartState.lineSeriesOptions;
    expect(options).toHaveLength(3);
    const titles = options.map((option) => option.title);
    expect(titles).toEqual(["MACD · Value", "MACD · Signal", "MACD · Histogram"]);
    expect(titles.join(" ")).toContain("MACD");
    expect(titles.join(" ")).toContain("Signal");
    expect(titles.join(" ")).toContain("Histogram");
    const colors = options.map((option) => option.color);
    expect(new Set(colors).size).toBe(3);
    expect(colors[0]).toBe("#56B4E9");
  });

  it("renders one chart per separate indicator on top of the main chart", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[rsi, macdSeparate]} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(3);
    expect(screen.getByTestId("separate-pane-rsi")).toBeInTheDocument();
    expect(screen.getByTestId("separate-pane-macd")).toBeInTheDocument();
    expect(screen.getByText(/server-computed series/i)).toBeInTheDocument();
    const titles = chartState.lineSeriesOptions.map((option) => option.title);
    expect(titles).toEqual([
      "RSI · Value",
      "MACD · Value",
      "MACD · Signal",
      "MACD · Histogram",
    ]);
  });
});
