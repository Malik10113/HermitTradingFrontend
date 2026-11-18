import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Trade } from "../../api/types";
import { TradeInspector, TradeTable } from "./TradeTable";

const trade: Trade = {
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
  execution_source: "fixed",
  held_to_settlement: false,
  phase: "pre_match",
};

describe("trade table", () => {
  it("opens the inspector when a row is clicked, not only its id button", async () => {
    const onSelectTrade = vi.fn();
    render(
      <TradeTable
        trades={[trade]}
        profile="conservative"
        selectedTradeId={null}
        onSelectTrade={onSelectTrade}
      />,
    );
    await userEvent.click(screen.getByText("2024-10-19 10:05 UTC"));
    expect(onSelectTrade).toHaveBeenCalledWith("T-0001");
  });

  it("shows the actual order side and token from the artifact", () => {
    render(
      <TradeTable
        trades={[trade]}
        profile="conservative"
        selectedTradeId={null}
        onSelectTrade={vi.fn()}
      />,
    );
    expect(screen.getAllByText("BUY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("YES").length).toBeGreaterThan(0);
    expect(screen.getAllByText("open long").length).toBeGreaterThan(0);
  });
});

describe("trade inspector", () => {
  it("renders side, token, intent, times, prices, contracts and net costs", () => {
    render(<TradeInspector trade={trade} onClose={vi.fn()} />);
    expect(screen.getByRole("region", { name: "Trade inspector" })).toBeInTheDocument();
    expect(screen.getByText("Entry time")).toBeInTheDocument();
    expect(screen.getByText("Exit time")).toBeInTheDocument();
    expect(screen.getByText("open long")).toBeInTheDocument();
    expect(screen.getByText("0.400")).toBeInTheDocument();
    expect(screen.getByText("0.500")).toBeInTheDocument();
    expect(screen.getByText("100 contracts")).toBeInTheDocument();
    expect(screen.getByText("+$12.00")).toBeInTheDocument();
    expect(screen.getByText("+$10.00")).toBeInTheDocument();
    expect(screen.getByText("$1.00")).toBeInTheDocument();
    expect(screen.getByText("fixed")).toBeInTheDocument();
  });

  it("shows not available for absent fields instead of inventing a fallback reason", () => {
    render(
      <TradeInspector
        trade={{
          trade_id: "T-0002",
          side: "BUY",
          close_side: null,
          token: "NO",
          entry_ts: "2024-10-19T10:05:00Z",
          entry_price: null,
          intent: null,
        }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getAllByText("not available").length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText(/stale or missing book snapshot/i)).toBeNull();
    expect(screen.queryByText(/^\$0\.00$/)).toBeNull();
  });

  it("does not claim a condition context that the artifact lacks", () => {
    render(<TradeInspector trade={trade} onClose={vi.fn()} />);
    expect(
      screen.getByText(/No condition context artifact is available/),
    ).toBeInTheDocument();
  });
});
