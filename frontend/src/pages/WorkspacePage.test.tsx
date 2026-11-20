import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { WorkspacePage } from "./WorkspacePage";
import { MetaProvider } from "../state/meta";
import { ThemeProvider } from "../state/theme";
import { getStorage } from "../lib/storage";

// The chart canvas is not the subject of this test; stub the charting surface
// so the workspace shell, data wiring and rail are exercised in jsdom.
vi.mock("lightweight-charts", () => {
  const series = {
    setData: () => {},
    setMarkers: () => {},
    priceScale: () => ({ applyOptions: () => {} }),
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
    addCandlestickSeries: () => series,
    addHistogramSeries: () => series,
    addLineSeries: () => series,
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

// The workspace passes the goals toggle to the chart; record the prop instead
// of rendering candles so the wiring can be asserted directly.
const chartProps = vi.hoisted(() => ({
  showGoals: null as boolean | null,
  dataVersion: null as string | null | undefined,
  resetKey: null as number | null,
  barIntervalSeconds: null as number | null,
  renders: 0,
}));

vi.mock("../components/PriceChart", () => ({
  PriceChart: (props: {
    showGoals?: boolean;
    dataVersion?: string | null;
    resetKey?: number;
    barIntervalSeconds?: number;
  }) => {
    chartProps.showGoals = props.showGoals ?? true;
    chartProps.dataVersion = props.dataVersion;
    chartProps.resetKey = props.resetKey ?? null;
    chartProps.barIntervalSeconds = props.barIntervalSeconds ?? 60;
    chartProps.renders += 1;
    return (
      <div
        data-testid="price-chart"
        data-show-goals={String(props.showGoals ?? true)}
        data-data-version={String(props.dataVersion ?? "")}
        data-reset-key={String(props.resetKey ?? 0)}
        data-bar-interval={String(props.barIntervalSeconds ?? 60)}
      />
    );
  },
}));

function SearchProbe() {
  const location = useLocation();
  return <div data-testid="search">{location.search}</div>;
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const instrumentId = "epl-bou-ars-2024-10-19-bou-YES.POLYMARKET";

const indicators = [
  {
    instance_id: "ema20",
    type: "ema",
    params: { period: 20 },
    color: "#56B4E9",
    visible: true,
  },
];

function stubApi(lifecycle: unknown, barsOverride?: unknown[]) {
  const barRows = barsOverride ?? [
    {
      ts_event: "2024-10-19T13:55:00Z",
      open: 0.4,
      high: 0.42,
      low: 0.39,
      close: 0.41,
      volume_usd: 120,
      trade_count: 4,
    },
    {
      ts_event: "2024-10-19T14:00:00Z",
      open: 0.41,
      high: 0.43,
      low: 0.4,
      close: 0.42,
      volume_usd: 90,
      trade_count: 3,
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/meta")) {
        return jsonResponse({
          data_version: "sha256:abc123def4567890",
          code_version: "git:deadbeef",
          nautilus_version: "2.0.0rc9",
        });
      }
      if (url.includes("/api/instruments?") || url.endsWith("/api/instruments")) {
        return jsonResponse({
          instruments: [
            {
              instrument_id: instrumentId,
              question: "Bournemouth vs Arsenal",
              league: "EPL",
              selection: "YES",
              venue: "POLYMARKET",
              coverage: { from: "2024-10-01", to: "2024-12-01" },
              trade_count: 1200,
              quality_grade: "A",
              settlement_known: true,
              settlement_value: 0,
              lifecycle,
            },
          ],
          total: 1,
        });
      }
      if (url.includes("/quality")) {
        return jsonResponse({
          instrument_id: instrumentId,
          available_from: "2024-10-01",
          available_to: "2024-12-01",
          trade_count: 1200,
          active_minute_pct: 42.5,
          longest_gap_s: 3600,
          median_trade_usd: 25,
          quality_grade: "A",
          warnings: [],
          gaps: [{ from: "2024-10-05", to: "2024-10-06", duration_s: 86400 }],
        });
      }
      if (url.includes("/api/bars")) {
        return jsonResponse({
          instrument_id: instrumentId,
          bar_type: "...-5-MINUTE-LAST-INTERNAL@1-MINUTE-LAST-EXTERNAL",
          data_version: "sha256:abc123def4567890",
          bars: barRows,
        });
      }
      if (url.endsWith("/api/indicators")) {
        return jsonResponse([
          {
            id: "ema",
            label: "EMA",
            pane: "overlay",
            parameters: [
              { name: "period", type: "int", min: 2, max: 400, default: 20, step: 1 },
            ],
            outputs: [{ key: "value" }],
            warmup: 19,
          },
        ]);
      }
      if (url.endsWith("/api/indicators/preview")) {
        return jsonResponse({
          series: { ema20: { value: [null, 0.42] } },
          warmup: { ema20: 1 },
          bars: 2,
        });
      }
      if (url.endsWith("/api/strategies")) {
        return jsonResponse({
          builtins: [
            {
              id: "nautilus_ema_cross",
              label: "Nautilus EMA cross",
              description: "Fast EMA crosses slow EMA",
              provenance: "Nautilus EMACross example",
              parameters: [
                { name: "fast", type: "int", min: 2, max: 100, default: 10, step: 1 },
              ],
            },
          ],
          saved: [],
        });
      }
      if (url.endsWith("/api/execution/preflight")) {
        return jsonResponse({
          requested: "conservative",
          effective: "conservative",
          fallback_reason: null,
          cost_estimate: { fee_bps: 0, slippage_bps: 10, per_side_bps: 10 },
        });
      }
      throw new Error(`Unexpected request ${url} ${init?.method ?? "GET"}`);
    }),
  );
}

const defaultLifecycle = {
  actual_kickoff_utc: "2024-10-19T14:00:00Z",
  actual_end_utc: "2024-10-19T16:00:00Z",
};

describe("workspace wiring", () => {
  it("renders an instrument, server indicator and enabled run control without fabricating data", async () => {
    stubApi(defaultLifecycle);

    const state = JSON.stringify(indicators);
    render(
      <MemoryRouter
        initialEntries={[
          `/?instrument=${encodeURIComponent(instrumentId)}&bar=5m&ind=${encodeURIComponent(state)}`,
        ]}
      >
        <ThemeProvider>
          <MetaProvider>
            <Routes>
              <Route path="/" element={<WorkspacePage />} />
            </Routes>
          </MetaProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("button", { name: /search instruments \(ctrl\+k\)/i }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText(/Bournemouth vs Arsenal/).length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText(/ema/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /remove ema20/i })).toBeInTheDocument();
    // The composite Nautilus bar type is shown as returned by the API.
    expect(screen.getAllByText(/5-MINUTE-LAST-INTERNAL/).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.getAllByText(/preview/i).length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run backtest/i })).toBeEnabled();
    });
    expect(screen.queryByText(/fabricated/i)).toBeNull();
  });

  it("defaults the Goals checkbox on and keeps goals=0 in the URL when unchecked", async () => {
    chartProps.showGoals = null;
    stubApi({
      actual_kickoff_utc: "2024-10-19T14:00:00Z",
      actual_end_utc: "2024-10-19T16:00:00Z",
      events: [
        {
          ts: "2024-10-19T14:55:00Z",
          type: "goal",
          label: "GOAL",
          team: "Bournemouth",
          detail: "Goal! Bournemouth 1-0 Arsenal",
        },
        {
          ts: "2024-10-19T15:05:00Z",
          type: "halftime",
          label: "HALF TIME",
          team: null,
          detail: null,
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/?instrument=${encodeURIComponent(instrumentId)}`]}>
        <ThemeProvider>
          <MetaProvider>
            <Routes>
              <Route
                path="/"
                element={
                  <>
                    <WorkspacePage />
                    <SearchProbe />
                  </>
                }
              />
            </Routes>
          </MetaProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    const toggle = await screen.findByLabelText(/goals/i);
    expect(toggle).toBeChecked();
    await waitFor(() => {
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-show-goals", "true");
    });
    expect(chartProps.showGoals).toBe(true);

    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    await waitFor(() => {
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-show-goals", "false");
    });
    expect(chartProps.showGoals).toBe(false);
    await waitFor(() => {
      expect(screen.getByTestId("search").textContent).toContain("goals=0");
    });
  });

  it("shows lifecycle facts and loaded-bar totals in the chart header, and resets the zoom", async () => {
    chartProps.dataVersion = null;
    chartProps.resetKey = null;
    stubApi(defaultLifecycle);
    // Reset restores remembered defaults; seed them so the chart stays mounted
    // (with the same instrument) while the zoom key changes.
    getStorage().setItem(
      "htf.workspace.defaults",
      JSON.stringify({
        instrument: instrumentId,
        barType: "5m",
        from: "2024-10-01",
        to: "2024-12-01",
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/?instrument=${encodeURIComponent(instrumentId)}&bar=5m`]}>
        <ThemeProvider>
          <MetaProvider>
            <Routes>
              <Route path="/" element={<WorkspacePage />} />
            </Routes>
          </MetaProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    await screen.findByTestId("price-chart");
    await waitFor(() => {
      expect(chartProps.dataVersion).toBe("sha256:abc123def4567890");
    });
    expect(chartProps.resetKey).toBe(0);
    // The 5-minute bar type is threaded to the chart for goal coverage.
    expect(chartProps.barIntervalSeconds).toBe(300);

    // Facts move from the chart footer into the panel header.
    expect(screen.getByText(/Kickoff 2024-10-19 14:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/End 2024-10-19 16:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/Duration 2h 0m/)).toBeInTheDocument();
    // Totals sum the two loaded bars: 4 + 3 fills and $120 + $90 notional.
    expect(screen.getByText(/Fills 7/)).toBeInTheDocument();
    expect(screen.getByText(/Volume \$210\.00/)).toBeInTheDocument();
    expect(screen.getAllByTitle(/sum over the loaded bars/)).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /reset view/i }));
    await waitFor(() => {
      expect(chartProps.resetKey).toBe(1);
    });
    expect(screen.getByTestId("price-chart")).toHaveAttribute("data-reset-key", "1");
  });

  it("reports header totals as not available, never zero, when no bars load", async () => {
    stubApi(defaultLifecycle, []);

    render(
      <MemoryRouter initialEntries={[`/?instrument=${encodeURIComponent(instrumentId)}&bar=5m`]}>
        <ThemeProvider>
          <MetaProvider>
            <Routes>
              <Route path="/" element={<WorkspacePage />} />
            </Routes>
          </MetaProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    await screen.findByText(/No bars in this range/i);
    const header = screen.getByRole("region", { name: "Price chart" });
    expect(header.textContent).toContain("Fills not available");
    expect(header.textContent).toContain("Volume not available");
    expect(header.textContent).not.toMatch(/Fills 0\b/);
    expect(header.textContent).not.toMatch(/Volume \$0\.00/);
    expect(screen.queryByTestId("price-chart")).toBeNull();
  });
});
