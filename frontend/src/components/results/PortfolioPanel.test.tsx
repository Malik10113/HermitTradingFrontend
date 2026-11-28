import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PortfolioPanel } from "./PortfolioPanel";
import type { PortfolioOptimizeResponse, RunSummary } from "../../api/types";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const RUN_ALPHA = "run-alpha-0001";
const RUN_BETA = "run-beta-0002";
const RUN_GAMMA = "run-gamma-0003";

/** A three-sleeve response where the second YES sleeve is collapsed. */
const RESPONSE: PortfolioOptimizeResponse = {
  method: "three_stage",
  provenance: "backend/engine/portfolio.py three_stage v1",
  effective_n: 2,
  max_pair_correlation: 0.93,
  portfolio_kelly_fraction: 0.25,
  recommended_daily_stake_usd: 250,
  reallocated_weight: 0.1,
  members: [
    {
      run_id: RUN_ALPHA,
      instrument_id: "polymarket:espn-123:YES",
      strategy_hash: "sha256:aaaa",
      requested_profile: "conservative",
      effective_profile: "conservative",
      weight: 0.6,
      kelly_fraction: 0.25,
      annualised_vol: 0.42,
      sharpe: 1.2,
      net_pnl_usd: 80,
      dropped: false,
      drop_reason: null,
    },
    {
      run_id: RUN_BETA,
      instrument_id: "polymarket:espn-456:NO",
      strategy_hash: "sha256:bbbb",
      requested_profile: "book",
      effective_profile: "book",
      weight: 0.4,
      kelly_fraction: 0.15,
      annualised_vol: 0.31,
      sharpe: 0.8,
      net_pnl_usd: 40,
      dropped: false,
      drop_reason: null,
    },
    {
      run_id: RUN_GAMMA,
      instrument_id: "polymarket:espn-123:YES",
      strategy_hash: "sha256:aaaa",
      requested_profile: "stress",
      effective_profile: "stress",
      weight: 0,
      kelly_fraction: 0,
      annualised_vol: null,
      sharpe: null,
      net_pnl_usd: null,
      dropped: true,
      drop_reason: "duplicate sleeve collapsed into run-alpha-0001",
    },
  ],
  weights: { [RUN_ALPHA]: 0.6, [RUN_BETA]: 0.4, [RUN_GAMMA]: 0 },
  kelly_fractions: { [RUN_ALPHA]: 0.25, [RUN_BETA]: 0.15, [RUN_GAMMA]: 0 },
  correlation_matrix: {
    [RUN_ALPHA]: { [RUN_ALPHA]: 1, [RUN_BETA]: 0.93 },
    [RUN_BETA]: { [RUN_ALPHA]: 0.93, [RUN_BETA]: 1 },
  },
  metrics: {
    starting_cash_usd: 1000,
    final_equity_usd: 1120,
    net_pnl_usd: 120,
    return_pct: 6,
    sharpe: 1.1,
    max_drawdown_pct: -4,
    days: 30,
  },
  equity_index: [
    { ts: "2026-11-01T00:00:00Z", equity_index: 100 },
    { ts: "2026-11-02T00:00:00Z", equity_index: 112 },
  ],
  warnings: ["Book coverage missing for run-beta-0002; the fill fell back to Conservative."],
};

const COMPLETE_RUNS: RunSummary[] = [
  { run_id: RUN_ALPHA, status: "complete" },
  { run_id: RUN_BETA, status: "complete" },
];

interface CapturedRequest {
  url: string;
  method?: string;
  body?: unknown;
}

function stubPortfolioApi(captured: CapturedRequest[], body: unknown = RESPONSE) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    captured.push({
      url: String(input),
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return jsonResponse(body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The card element that carries a summary label; its text holds label and value. */
function summaryCard(label: string): HTMLElement {
  const card = screen.getByText(label).closest("div");
  if (!card) throw new Error(`Summary card ${label} not found`);
  return card as HTMLElement;
}

describe("PortfolioPanel request", () => {
  it("posts the selected method, sleeve cap and Kelly fraction for the selected runs", async () => {
    const captured: CapturedRequest[] = [];
    stubPortfolioApi(captured);
    const user = userEvent.setup();

    render(<PortfolioPanel selectedRunIds={[RUN_ALPHA, RUN_BETA]} runs={COMPLETE_RUNS} />);

    await user.selectOptions(screen.getByLabelText("Allocation method"), "risk_parity");

    const maxWeight = screen.getByLabelText("Max sleeve weight");
    await user.clear(maxWeight);
    await user.type(maxWeight, "0.5");

    await user.click(screen.getByRole("button", { name: "Optimise portfolio" }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({
      url: "/api/portfolio/optimize",
      method: "POST",
      body: {
        run_ids: [RUN_ALPHA, RUN_BETA],
        method: "risk_parity",
        max_weight: 0.5,
        kelly_fraction: 0.25,
      },
    });
  });

  it("disables the button below two selected runs", () => {
    const captured: CapturedRequest[] = [];
    stubPortfolioApi(captured);
    render(<PortfolioPanel selectedRunIds={[RUN_ALPHA]} runs={COMPLETE_RUNS} />);

    expect(screen.getByRole("button", { name: "Optimise portfolio" })).toBeDisabled();
    expect(screen.getByText(/Select at least two completed runs/)).toBeInTheDocument();
  });
});

describe("PortfolioPanel result", () => {
  async function renderResult() {
    const captured: CapturedRequest[] = [];
    stubPortfolioApi(captured);
    const user = userEvent.setup();
    render(<PortfolioPanel selectedRunIds={[RUN_ALPHA, RUN_BETA]} runs={COMPLETE_RUNS} />);
    await user.click(screen.getByRole("button", { name: "Optimise portfolio" }));
    await screen.findByText(/Provenance:/);
  }

  it("renders the optimiser's own weights, Kelly fractions and effective sleeve count", async () => {
    await renderResult();

    expect(summaryCard("Effective sleeves")).toHaveTextContent("2.00");
    expect(summaryCard("Max pair correlation")).toHaveTextContent("0.93");
    expect(summaryCard("Portfolio LCB Kelly")).toHaveTextContent("25.0%");
    expect(summaryCard("Portfolio LCB Kelly")).toHaveTextContent("$250.00");

    // Member weights and Kelly fractions come straight from the response.
    expect(screen.getByText("60.00%")).toBeInTheDocument();
    expect(screen.getByText("40.00%")).toBeInTheDocument();
    expect(screen.getByText("25.00%")).toBeInTheDocument();
    expect(screen.getByText("15.00%")).toBeInTheDocument();

    // Effective profiles stay attached to every sleeve.
    expect(screen.getAllByText("Conservative").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Book").length).toBeGreaterThan(0);
  });

  it("shows a collapsed duplicate with its drop reason rather than a weight", async () => {
    await renderResult();

    const reason = screen.getByText("duplicate sleeve collapsed into run-alpha-0001");
    expect(reason).toBeInTheDocument();
    expect(screen.getAllByText("dropped")).toHaveLength(1);

    const row = reason.closest("tr");
    expect(row).not.toBeNull();
    if (!row) return;
    // Missing vol, Sharpe and net P&L stay "not available"; nothing is invented.
    expect(within(row as HTMLElement).getAllByText("not available")).toHaveLength(3);
  });

  it("renders the pair correlation matrix and the combined portfolio metrics", async () => {
    await renderResult();

    const matrix = screen.getByRole("table", { name: "Pair correlation matrix" });
    expect(within(matrix).getAllByText("1.00")).toHaveLength(2);
    expect(within(matrix).getAllByText("0.93")).toHaveLength(2);
    expect(
      within(matrix).getByRole("rowheader", { name: /run-alpha-00/ }),
    ).toBeInTheDocument();
    expect(
      within(matrix).getByRole("columnheader", { name: /run-beta-000/ }),
    ).toBeInTheDocument();

    expect(summaryCard("Combined Net P&L")).toHaveTextContent("+$120.00");
    expect(summaryCard("Combined return")).toHaveTextContent("+6.00%");
    expect(summaryCard("Combined Sharpe")).toHaveTextContent("1.10");
    expect(summaryCard("Max drawdown")).toHaveTextContent("-4.00%");
  });

  it("carries the warnings, provenance and the no-profitability disclaimer", async () => {
    await renderResult();

    const warnings = screen.getByRole("list", { name: "Portfolio warnings" });
    expect(warnings).toHaveTextContent("Book coverage missing for run-beta-0002");
    expect(screen.getByText("backend/engine/portfolio.py three_stage v1")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Historical simulation allocation across completed runs; not financial advice or a profitability claim\./,
      ),
    ).toBeInTheDocument();
  });
});
