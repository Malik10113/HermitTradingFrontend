import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Instrument, InstrumentQuality } from "../api/types";
import { MarketBar } from "./MarketBar";

const yesInstrument: Instrument = {
  instrument_id: "epl-x-YES.POLYMARKET",
  question: "Will X win?",
  league: "EPL",
  selection: "YES",
  venue: "POLYMARKET",
  coverage: { from: "2024-10-01", to: "2024-12-01" },
  trade_count: 1200,
  quality_grade: "A",
  settlement_known: false,
  settlement_value: null,
};

const noInstrument: Instrument = {
  ...yesInstrument,
  instrument_id: "epl-x-NO.POLYMARKET",
  selection: "NO",
  settlement_known: true,
  settlement_value: 1,
};

const quality: InstrumentQuality = {
  instrument_id: yesInstrument.instrument_id,
  available_from: "2024-10-01",
  available_to: "2024-12-01",
  trade_count: 1200,
  quality_grade: "A",
  warnings: ["very_few_trades"],
  settlement_known: false,
};

function renderBar(selected: Instrument | null = yesInstrument) {
  return render(
    <MarketBar
      instruments={[yesInstrument, noInstrument]}
      instrumentsLoading={false}
      instrumentsError={null}
      onReloadInstruments={vi.fn()}
      selected={selected}
      selectedId={selected?.instrument_id ?? ""}
      onSelect={vi.fn()}
      league=""
      onLeague={vi.fn()}
      onOpenPalette={vi.fn()}
      quality={quality}
      qualityLoading={false}
    />,
  );
}

describe("market bar", () => {
  it("shows the YES/NO token, coverage, fills, quality grade and warnings", () => {
    renderBar();
    expect(screen.getByText("YES token")).toBeInTheDocument();
    expect(screen.getByText(/2024-10-01 → 2024-12-01/)).toBeInTheDocument();
    expect(screen.getByText("1,200 fills")).toBeInTheDocument();
    expect(screen.getByText("grade A")).toBeInTheDocument();
    expect(screen.getByText(/1 data warning/)).toBeInTheDocument();
  });

  it("states an unknown settlement explicitly instead of inventing a value", () => {
    renderBar();
    expect(screen.getByTestId("settlement-badge").textContent).toBe("settlement unknown");
  });

  it("shows a recorded settlement value for a known outcome", () => {
    renderBar(noInstrument);
    expect(screen.getByText("NO token")).toBeInTheDocument();
    expect(screen.getByTestId("settlement-badge").textContent).toBe("settles 1");
  });

  it("opens the search palette and filters by league", async () => {
    const onOpenPalette = vi.fn();
    const onLeague = vi.fn();
    render(
      <MarketBar
        instruments={[yesInstrument, noInstrument]}
        instrumentsLoading={false}
        instrumentsError={null}
        onReloadInstruments={vi.fn()}
        selected={yesInstrument}
        selectedId={yesInstrument.instrument_id}
        onSelect={vi.fn()}
        league=""
        onLeague={onLeague}
        onOpenPalette={onOpenPalette}
        quality={quality}
        qualityLoading={false}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /search instruments \(ctrl\+k\)/i }));
    expect(onOpenPalette).toHaveBeenCalled();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /filter instruments by league/i }),
      "EPL",
    );
    expect(onLeague).toHaveBeenCalledWith("EPL");
  });
});
