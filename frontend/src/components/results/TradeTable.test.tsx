import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Trade } from "../../api/types";
import { TradeInspector, TradeTable } from "./TradeTable";

const trade: Trade = {
  trade_id: "T-0001",
  side: "BUY",
  close_side: "SELL",
  direction: "long",
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
  slippage_usd: 0.25,
  execution_source: "conservative_fallback",
  fallback_reason: "book_snapshot_stale",
  entry_execution_source: "conservative_fallback",
  entry_fallback_reason: "book_snapshot_stale",
  exit_execution_source: "book",
  exit_fallback_reason: null,
  held_to_settlement: false,
  phase: "pre_match",
  entry_context: {
    signal_ts: "2024-10-19T10:00:00Z",
    action: "long_entry",
    condition: "long_entry",
    indicator_values: { ema20: { value: 0.41 } },
  },
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
    expect(screen.getByText("$0.25")).toBeInTheDocument();
  });

  it("shows entry and exit execution provenance separately", () => {
    render(<TradeInspector trade={trade} onClose={vi.fn()} />);
    expect(screen.getByText("Entry source")).toBeInTheDocument();
    expect(screen.getByText("Exit source")).toBeInTheDocument();
    expect(screen.getByText("Entry fallback")).toBeInTheDocument();
    expect(screen.getByText("Exit fallback")).toBeInTheDocument();
    expect(screen.getByText("conservative_fallback")).toBeInTheDocument();
    expect(screen.getByText("book")).toBeInTheDocument();
    expect(screen.getByText("book_snapshot_stale")).toBeInTheDocument();
  });

  it("shows the entry signal condition and indicator snapshot", () => {
    render(<TradeInspector trade={trade} onClose={vi.fn()} />);
    expect(screen.getByText("Condition context at entry")).toBeInTheDocument();
    expect(screen.getByText("long_entry")).toBeInTheDocument();
    expect(screen.getByText("ema20.value")).toBeInTheDocument();
    expect(screen.getByText("0.4100")).toBeInTheDocument();
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
    expect(screen.getAllByText("not available").length).toBeGreaterThanOrEqual(6);
    expect(screen.queryByText(/stale or missing book snapshot/i)).toBeNull();
    expect(screen.queryByText(/^\$0\.00$/)).toBeNull();
  });

  it("does not claim a condition context that the artifact lacks", () => {
    render(
      <TradeInspector
        trade={{ ...trade, entry_context: null }}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/No condition context artifact is available/),
    ).toBeInTheDocument();
  });

  it("labels a settlement exit as having no fill source", () => {
    render(
      <TradeInspector
        trade={{
          ...trade,
          held_to_settlement: true,
          exit_execution_source: null,
          exit_fallback_reason: null,
        }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("settlement (no fill)")).toBeInTheDocument();
  });
});
