import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { IndicatorInstance, IndicatorSpec } from "../api/types";
import { IndicatorChips } from "./IndicatorChips";

const specs: IndicatorSpec[] = [
  {
    id: "ema",
    label: "EMA",
    pane: "overlay",
    parameters: [
      { name: "period", label: "Period", type: "int", min: 2, max: 400, default: 20, step: 1 },
    ],
    outputs: [{ key: "value" }],
    warmup: 19,
  },
];

const instance: IndicatorInstance = {
  instance_id: "ema",
  type: "ema",
  params: { period: 20 },
  color: "#56B4E9",
  visible: true,
};

function renderChips(overrides: Partial<Parameters<typeof IndicatorChips>[0]> = {}) {
  return render(
    <IndicatorChips
      specs={specs}
      specsLoading={false}
      specsError={null}
      onReloadSpecs={vi.fn()}
      instances={[]}
      onChange={vi.fn()}
      preview={null}
      previewLoading={false}
      previewError={null}
      previewMismatch={null}
      {...overrides}
    />,
  );
}

describe("indicator chips", () => {
  it("adds an instance with the registry defaults and a palette colour", async () => {
    const onChange = vi.fn();
    renderChips({ onChange });
    await userEvent.selectOptions(screen.getByLabelText("Add indicator"), "ema");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({
        instance_id: "ema",
        type: "ema",
        params: { period: 20 },
        visible: true,
      }),
    ]);
  });

  it("edits a parameter by keyboard and reports out-of-range values visibly", async () => {
    function StatefulChips() {
      const [instances, setInstances] = useState<IndicatorInstance[]>([instance]);
      return (
        <IndicatorChips
          specs={specs}
          specsLoading={false}
          specsError={null}
          onReloadSpecs={vi.fn()}
          instances={instances}
          onChange={setInstances}
          preview={null}
          previewLoading={false}
          previewError={null}
          previewMismatch={null}
        />
      );
    }
    render(<StatefulChips />);
    await userEvent.click(screen.getByRole("button", { name: /EMA/ }));
    const input = screen.getByLabelText("Period");
    await userEvent.clear(input);
    await userEvent.type(input, "500");
    expect(screen.getByLabelText("Period")).toHaveValue(500);
    expect(screen.getByRole("alert").textContent).toContain("maximum 400");
    expect(screen.getByLabelText("Period")).toHaveAttribute("aria-invalid", "true");
  });

  it("toggles visibility and removes an instance from the same chip", async () => {
    const onChange = vi.fn();
    renderChips({ instances: [instance], onChange });
    await userEvent.click(screen.getByRole("button", { name: /Hide ema/i }));
    expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ instance_id: "ema", visible: false }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: /Remove ema/i }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("shows a preview failure with its message instead of hiding it", () => {
    renderChips({ instances: [instance], previewError: new Error("indicator service down") });
    expect(screen.getByRole("alert").textContent).toContain("indicator service down");
    expect(screen.getByRole("alert").textContent).toContain("will not invent values");
  });

  it("summarises server warm-up and bar counts", () => {
    renderChips({
      instances: [instance],
      preview: {
        series: { ema: { value: [null, null, 0.4] } },
        warmup: { ema: 2 },
        bars: 3,
      },
    });
    expect(screen.getByText(/server preview: 3 values/i)).toBeInTheDocument();
    expect(screen.getByText(/ema: 2 bars/i)).toBeInTheDocument();
  });
});
