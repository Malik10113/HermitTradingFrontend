import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Instrument } from "../api/types";
import { CommandPalette } from "./CommandPalette";

const instruments: Instrument[] = [
  { instrument_id: "a-YES.POLYMARKET", question: "Alpha vs Beta", league: "EPL", selection: "YES" },
  { instrument_id: "b-YES.POLYMARKET", question: "Gamma vs Delta", league: "EPL", selection: "YES" },
  { instrument_id: "c-NO.POLYMARKET", question: "Gamma vs Delta", league: "EPL", selection: "NO" },
];

describe("command palette keyboard navigation", () => {
  it("moves the active option with arrows and selects it with Enter", async () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPalette
        open
        onClose={onClose}
        instruments={instruments}
        selectedId={undefined}
        onSelect={onSelect}
      />,
    );
    const input = await screen.findByRole("combobox", { name: "Instrument search" });
    await userEvent.type(input, "{ArrowDown}{Enter}");
    expect(onSelect).toHaveBeenCalledWith("b-YES.POLYMARKET");
    expect(onClose).toHaveBeenCalled();
  });

  it("jumps to the last option with End", async () => {
    const onSelect = vi.fn();
    render(
      <CommandPalette
        open
        onClose={vi.fn()}
        instruments={instruments}
        selectedId={undefined}
        onSelect={onSelect}
      />,
    );
    const input = await screen.findByRole("combobox", { name: "Instrument search" });
    await userEvent.type(input, "{End}{Enter}");
    expect(onSelect).toHaveBeenCalledWith("c-NO.POLYMARKET");
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <CommandPalette
        open
        onClose={onClose}
        instruments={instruments}
        selectedId={undefined}
        onSelect={vi.fn()}
      />,
    );
    const input = await screen.findByRole("combobox", { name: "Instrument search" });
    await userEvent.type(input, "{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("exposes each result as a listbox option with its token", async () => {
    render(
      <CommandPalette
        open
        onClose={vi.fn()}
        instruments={instruments}
        selectedId={instruments[1].instrument_id}
        onSelect={vi.fn()}
      />,
    );
    await screen.findByRole("combobox", { name: "Instrument search" });
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByText("selected")).toBeInTheDocument();
  });
});
