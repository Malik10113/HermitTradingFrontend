import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HoldoutPanel } from "./HoldoutPanel";

describe("holdout panel", () => {
  it("shows the hidden state with a reveal action", async () => {
    const onReveal = vi.fn();
    render(
      <HoldoutPanel
        holdout={{
          revealed: false,
          reveal_count: 0,
          split: { train_end: "2024-10-01", test_start: "2024-10-02" },
          warning: null,
        }}
        onReveal={onReveal}
        revealing={false}
        revealError={null}
      />,
    );
    expect(screen.getByText("hidden")).toBeInTheDocument();
    expect(screen.getByText(/train ends 2024-10-01/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /reveal out-of-sample/i }));
    expect(onReveal).toHaveBeenCalledTimes(1);
  });

  it("warns before a repeat reveal and requires confirmation", async () => {
    const onReveal = vi.fn();
    render(
      <HoldoutPanel
        holdout={{
          revealed: true,
          reveal_count: 2,
          split: { train_end: "2024-10-01", test_start: "2024-10-02" },
          warning: "Window already viewed.",
        }}
        onReveal={onReveal}
        revealing={false}
        revealError={null}
      />,
    );
    expect(screen.getByText(/already been revealed 2 time/i)).toBeInTheDocument();
    expect(screen.getByText("Window already viewed.")).toBeInTheDocument();
    expect(onReveal).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /^Reveal again$/i }));
    expect(onReveal).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /no longer clean/i }));
    expect(onReveal).toHaveBeenCalledTimes(1);
  });

  it("reports a missing holdout ledger instead of implying a clean one", () => {
    render(
      <HoldoutPanel holdout={null} onReveal={vi.fn()} revealing={false} revealError={null} />,
    );
    expect(screen.getByText(/Holdout state not available/)).toBeInTheDocument();
  });

  it("surfaces a reveal error", () => {
    render(
      <HoldoutPanel
        holdout={{ revealed: false, reveal_count: 0 }}
        onReveal={vi.fn()}
        revealing={false}
        revealError={new Error("ledger unavailable")}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("ledger unavailable");
  });
});
