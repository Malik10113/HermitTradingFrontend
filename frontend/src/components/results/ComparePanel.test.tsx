import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ComparePanel, compareRows } from "./ComparePanel";
import { normaliseEquityCurves } from "../../lib/chartMarkers";
import { normaliseRun } from "../../api/normalise";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function runPayload(overrides: Record<string, unknown>) {
  return {
    run_id: "run_a",
    status: "complete",
    label: "Run A",
    requested_profile: "conservative",
    effective_profile: "conservative",
    data_version: "sha256:abcdef1234567890",
    strategy_hash: "sha256:1111222233334444",
    summary: {
      gross_pnl_usd: 100,
      net_pnl_usd: 80,
      gross_return_pct: 10,
      net_return_pct: 8,
      gross_sharpe: 1.5,
      sharpe: 1.2,
      max_drawdown_pct: -4,
      trade_count: 6,
    },
    costs: {
      fees_usd: 1.5,
      slippage_usd: 0.5,
      total_cost_bps: 42,
      requested_profile: "conservative",
      effective_profile: "conservative",
    },
    equity: [
      { ts: "2024-10-01T00:00:00Z", equity_usd: 1000 },
      { ts: "2024-10-02T00:00:00Z", equity_usd: 1080 },
    ],
    ...overrides,
  };
}

describe("compare normalisation", () => {
  it("flattens gross and net metrics and keeps a missing value null", () => {
    const rows = compareRows([
      normaliseRun(runPayload({})),
      normaliseRun(
        runPayload({
          run_id: "run_b",
          label: "Run B",
          summary: { net_pnl_usd: -5, trade_count: 2 },
        }),
      ),
    ]);

    expect(rows[0]).toMatchObject({
      grossPnl: 100,
      netPnl: 80,
      grossReturn: 10,
      netReturn: 8,
      grossSharpe: 1.5,
      netSharpe: 1.2,
      maxDrawdown: -4,
      trades: 6,
      fees: 1.5,
      totalCostBps: 42,
    });
    expect(rows[1].grossPnl).toBeNull();
    expect(rows[1].netPnl).toBe(-5);
    expect(rows[1].netReturn).toBeNull();
  });

  it("indexes the equity overlay to 100 at the common start", () => {
    const series = normaliseEquityCurves([
      {
        label: "A",
        equity: [
          { ts: "2024-10-01T00:00:00Z", equity_usd: 100 },
          { ts: "2024-10-02T00:00:00Z", equity_usd: 110 },
          { ts: "2024-10-03T00:00:00Z", equity_usd: 121 },
        ],
      },
      {
        label: "B",
        equity: [
          { ts: "2024-10-02T00:00:00Z", equity_usd: 200 },
          { ts: "2024-10-03T00:00:00Z", equity_usd: 220 },
        ],
      },
    ]);

    expect(series).toHaveLength(2);
    expect(series[0].points).toHaveLength(2);
    expect(series[0].points[0].value).toBeCloseTo(100, 6);
    expect(series[0].points[1].value).toBeCloseTo(110, 6);
    expect(series[1].points).toHaveLength(2);
    expect(series[1].points[0].value).toBeCloseTo(100, 6);
    expect(series[1].points[1].value).toBeCloseTo(110, 6);
  });
});

describe("ComparePanel", () => {
  it("renders gross and net side by side with profile and cost context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/runs/compare")) {
          return jsonResponse({
            runs: [
              runPayload({}),
              runPayload({
                run_id: "run_b",
                label: "Run B",
                requested_profile: "book",
                effective_profile: "conservative",
                fallback_reason: "no_book_coverage",
                summary: {
                  gross_pnl_usd: 40,
                  net_pnl_usd: 25,
                  gross_return_pct: 4,
                  net_return_pct: 2.5,
                  gross_sharpe: 0.8,
                  sharpe: 0.6,
                  max_drawdown_pct: -6,
                  trade_count: 3,
                },
                costs: {
                  fees_usd: 2,
                  slippage_usd: 1,
                  total_cost_bps: 75,
                  fallback_rate: 0.25,
                },
              }),
            ],
          });
        }
        throw new Error(`Unexpected request in test: ${url}`);
      }),
    );

    render(
      <ComparePanel compareIds={["run_a", "run_b"]} onChange={() => {}} currentRunId={null} />,
    );

    await waitFor(() => expect(screen.getAllByText("Run A").length).toBeGreaterThan(0));
    expect(screen.getByText("+$100.00")).toBeInTheDocument();
    expect(screen.getByText("+$80.00")).toBeInTheDocument();
    expect(screen.getByText("+10.00%")).toBeInTheDocument();
    expect(screen.getByText("+8.00%")).toBeInTheDocument();
    expect(screen.getByText("+$40.00")).toBeInTheDocument();
    expect(screen.getByText("+$25.00")).toBeInTheDocument();

    expect(screen.getAllByText(/no book coverage/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Net is gross less fees and slippage/)).toBeInTheDocument();
    expect(screen.getByText(/Comparing the same strategy hash across profiles isolates cost/)).toBeInTheDocument();
    expect(screen.getByText(/Fill fallback rate 25.0%/)).toBeInTheDocument();
    expect(screen.getAllByText(/Conservative/).length).toBeGreaterThan(0);
  });
});
