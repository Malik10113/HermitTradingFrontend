import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { WorkspacePage } from "./WorkspacePage";
import { MetaProvider } from "../state/meta";
import { ThemeProvider } from "../state/theme";

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

describe("workspace wiring", () => {
  it("renders an instrument, server indicator and enabled run control without fabricating data", async () => {
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
                lifecycle: {
                  actual_kickoff_utc: "2024-10-19T14:00:00Z",
                  actual_end_utc: "2024-10-19T16:00:00Z",
                },
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
            bars: [
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
            ],
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
    expect(screen.getAllByText("EMA").length).toBeGreaterThan(0);
    expect(screen.getByText(/preview mismatch|preview/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run backtest/i })).toBeEnabled();
    });
    expect(screen.queryByText(/fabricated/i)).toBeNull();
  });
});
