import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { RunDetail } from "../../api/types";
import { ResultsDrawer } from "./ResultsDrawer";

const run: RunDetail = {
  run_id: "run_test",
  status: "complete",
  run_hash: "sha256:runhash",
  strategy_hash: "sha256:strategyhash",
  data_version: "sha256:data",
  created_at: "2024-10-20T09:00:00Z",
  completed_at: "2024-10-20T09:00:18Z",
  requested_profile: "book",
  effective_profile: "conservative",
  fallback_reason: "no_book_coverage",
  execution: {
    requested: "book",
    effective: "conservative",
    fallback_reason: "no_book_coverage",
    book_coverage: { first_month: "2024-01", last_month: "2024-10", has_coverage: true },
  },
  metrics: {
    gross_pnl_usd: 12,
    net_pnl_usd: 10,
    net_return_pct: 1,
    sharpe: 0.9,
    max_drawdown_pct: -0.4,
    trade_count: 1,
    win_rate: 1,
    fees_usd: 1,
    slippage_usd: 1,
  },
  costs: {
    fees_usd: 1,
    spread_usd: 0,
    impact_usd: null,
    total_cost_usd: 2,
    total_cost_bps: 10,
    fallback_rate: 0.5,
    requested_profile: "book",
    effective_profile: "conservative",
    fallback_reason: "no_book_coverage",
  },
  diagnostics: {
    signals_generated: 2,
    fills: 1,
    trades_completed: 1,
    avg_holding_minutes: 60,
  },
  phases: [{ phase: "pre_match", trades: 1, pnl_usd: 10, return_pct: 1, sharpe: null }],
  holdout: null,
  equity: [
    { ts: "2024-10-19T10:00:00Z", equity_usd: 1000, drawdown_pct: 0 },
    { ts: "2024-10-19T11:00:00Z", equity_usd: 1010, drawdown_pct: 0 },
  ],
  drawdown: [
    { ts: "2024-10-19T10:00:00Z", drawdown_pct: 0 },
    { ts: "2024-10-19T11:00:00Z", drawdown_pct: 0.2 },
  ],
  trades: [
    {
      trade_id: "T-0001",
      side: "BUY",
      close_side: "SELL",
      token: "YES",
      intent: "open_long",
      entry_ts: "2024-10-19T10:05:00Z",
      exit_ts: "2024-10-19T10:55:00Z",
      entry_price: 0.4,
      exit_price: 0.5,
      contracts: 100,
      gross_pnl_usd: 12,
      net_pnl_usd: 10,
      fees_usd: 1,
      execution_source: "conservative_fallback",
      fallback_reason: "stale_snapshot",
      held_to_settlement: false,
      phase: "pre_match",
      entry_context: { ema20: 0.41 },
    },
  ],
  config: null,
};

function renderDrawer(overrides: Partial<RunDetail> = {}) {
  const value = { ...run, ...overrides };
  return render(
    <ResultsDrawer
      runId={value.run_id}
      run={value}
      loading={false}
      error={null}
      refresh={vi.fn()}
      reveal={vi.fn()}
      revealing={false}
      revealError={null}
      open
      onOpen={vi.fn()}
      onClose={vi.fn()}
      compareIds={[]}
      onCompareChange={vi.fn()}
    />,
  );
}

describe("results drawer", () => {
  it("shows net metrics with the effective profile and the fallback warning", () => {
    renderDrawer();
    expect(screen.getAllByText("Net · Conservative").length).toBeGreaterThan(0);
    expect(screen.getByTestId("fallback-badge").textContent).toContain("Book");
    expect(screen.getByText("Net P&L · historical simulation")).toBeInTheDocument();
    expect(screen.getByText("Net return")).toBeInTheDocument();
  });

  it("renders the actual trade side and token and opens the inspector", async () => {
    renderDrawer();
    await userEvent.click(screen.getByRole("tab", { name: /^Trades/ }));
    expect(screen.getAllByText("BUY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("YES").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "T-0001" }));
    expect(screen.getByRole("region", { name: "Trade inspector" })).toBeInTheDocument();
    expect(screen.getByText("Condition context at entry")).toBeInTheDocument();
    expect(screen.getByText("stale_snapshot", { exact: false })).toBeInTheDocument();
  });

  it("reports missing holdout state instead of inventing a clean ledger", () => {
    renderDrawer();
    expect(screen.getByText(/Holdout state not available/)).toBeInTheDocument();
  });

  it("labels an unrevealed holdout's metrics as in-sample only", () => {
    renderDrawer({ metrics_scope: "in_sample", oos_hidden: true });
    expect(screen.getByText(/in-sample only/i)).toBeInTheDocument();
    expect(screen.getByText(/Out-of-sample results stay hidden/i)).toBeInTheDocument();
  });

  it("shows the cost breakdown including fees, slippage and fallback rate", async () => {
    renderDrawer();
    await userEvent.click(screen.getByRole("tab", { name: /^Costs/ }));
    expect(screen.getByLabelText("Cost breakdown")).toBeInTheDocument();
    expect(screen.getByText("Fees")).toBeInTheDocument();
    expect(screen.getByText("Spread")).toBeInTheDocument();
    expect(screen.getByText("Impact")).toBeInTheDocument();
    expect(screen.getByText("Fallback rate")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getAllByText(/Requested Book, effective Conservative/i).length).toBeGreaterThan(0);
  });

  it("shows the phase breakdown and an explicit unknown phase row", async () => {
    renderDrawer();
    await userEvent.click(screen.getByRole("tab", { name: /^Phases/ }));
    expect(screen.getByText("pre_match")).toBeInTheDocument();
    expect(screen.getByText(/observed ESPN kickoff/i)).toBeInTheDocument();
  });

  it("opens the compare panel and asks for 2–3 runs", async () => {
    renderDrawer();
    await userEvent.click(screen.getByRole("tab", { name: /^Compare/ }));
    expect(screen.getByText(/Compare two or three runs/i)).toBeInTheDocument();
  });
});
