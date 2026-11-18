import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { Preset } from "../api/types";
import { parseWorkspaceSearch } from "../lib/urlState";
import { PresetsPage, presetWorkspaceSearch } from "./PresetsPage";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const instrumentId = "epl-bou-ars-2024-10-19-bou-YES.POLYMARKET";

const preset: Preset = {
  id: "pre_1",
  name: "EPL EMA cross — conservative",
  created_at: "2024-11-01T10:00:00Z",
  config: {
    strategy: {
      instrument_id: instrumentId,
      bar_type: "5m",
      builtin_id: "nautilus_ema_cross",
      indicators: [
        { instance_id: "ema_fast", type: "ema", params: { period: 12 }, color: "#56B4E9" },
      ],
    },
    execution: { profile: "stress" },
    risk: { sizing: { mode: "fixed_stake", value: 100 } },
    evaluation: { date_range: { from: "2024-09-01", to: "2024-12-01" } },
  },
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

describe("preset URL restore", () => {
  it("round-trips instrument, bar type, range, indicators and profile", () => {
    const search = presetWorkspaceSearch(preset);
    const parsed = parseWorkspaceSearch(search);

    expect(parsed.issues).toEqual([]);
    expect(parsed.state.instrument).toBe(instrumentId);
    expect(parsed.state.barType).toBe("5m");
    expect(parsed.state.from).toBe("2024-09-01");
    expect(parsed.state.to).toBe("2024-12-01");
    expect(parsed.state.profile).toBe("stress");
    expect(parsed.state.builtin).toBe("nautilus_ema_cross");
    expect(parsed.state.indicators).toEqual([
      { instance_id: "ema_fast", type: "ema", params: { period: 12 }, color: "#56B4E9" },
    ]);
  });

  it("keeps explicit rules even when a builtin id is present for provenance", () => {
    const withRules: Preset = {
      ...preset,
      config: {
        ...preset.config,
        strategy: {
          ...preset.config.strategy,
          long_entry: { gt: ["close", 0.5] },
        },
      },
    };
    const parsed = parseWorkspaceSearch(presetWorkspaceSearch(withRules));
    expect(parsed.state.strategyJson).not.toBeNull();
    expect(parsed.state.strategyJson).toContain('"long_entry"');
  });
});

describe("PresetsPage", () => {
  it("lists the {items,presets} form and opens a preset into the workspace URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/presets")) return jsonResponse({ items: [preset], presets: [preset] });
        if (url.endsWith("/api/indicators")) return jsonResponse({ indicators: [] });
        throw new Error(`Unexpected request in test: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/presets"]}>
        <Routes>
          <Route path="/presets" element={<PresetsPage />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("EPL EMA cross — conservative");
    expect(screen.getByText(/2024-09-01 → 2024-12-01/)).toBeInTheDocument();

    await userEvent.setup().click(
      screen.getByRole("link", { name: /Open preset EPL EMA cross/ }),
    );

    const location = await screen.findByTestId("location");
    const [pathname, search] = location.textContent!.split("?");
    expect(pathname).toBe("/");
    const parsed = parseWorkspaceSearch(`?${search}`);
    expect(parsed.state.instrument).toBe(instrumentId);
    expect(parsed.state.barType).toBe("5m");
    expect(parsed.state.from).toBe("2024-09-01");
    expect(parsed.state.to).toBe("2024-12-01");
    expect(parsed.state.profile).toBe("stress");
    expect(parsed.state.builtin).toBe("nautilus_ema_cross");
  });

  it("creates a preset with the API's {name, config} body", async () => {
    let posted: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/presets") && init?.method === "POST") {
          posted = init.body ? JSON.parse(String(init.body)) : null;
          return jsonResponse({ id: "pre_new", name: "Sweep baseline" }, 201);
        }
        if (url.endsWith("/api/presets")) return jsonResponse({ items: [], presets: [] });
        if (url.endsWith("/api/indicators")) return jsonResponse({ indicators: [] });
        throw new Error(`Unexpected request in test: ${url}`);
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/presets"]}>
        <Routes>
          <Route path="/presets" element={<PresetsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText("Preset name"), "Sweep baseline");
    fireEvent.change(screen.getByLabelText("Preset configuration JSON"), {
      target: { value: JSON.stringify(preset.config) },
    });
    const save = screen.getByRole("button", { name: "Save preset" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      name: "Sweep baseline",
      config: {
        strategy: { instrument_id: instrumentId, bar_type: "5m" },
        evaluation: { date_range: { from: "2024-09-01", to: "2024-12-01" } },
      },
    });
    expect(await screen.findByText("Preset saved.")).toBeInTheDocument();
  });
});
