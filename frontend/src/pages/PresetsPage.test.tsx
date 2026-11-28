import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { BacktestConfig, InstrumentQuality, PreflightResponse, Preset } from "../api/types";
import { parseWorkspaceSearch } from "../lib/urlState";
import { StrategyRail } from "../components/StrategyRail";
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

const railQuality: InstrumentQuality = {
  instrument_id: instrumentId,
  available_from: "2024-09-01T00:00:00Z",
  available_to: "2024-12-01T00:00:00Z",
  trade_count: 1234,
  bar_count: 900,
  active_minutes: 600,
  active_minute_pct: 61.5,
  longest_gap_s: 900,
  median_trade_usd: 12.5,
  quality_grade: "B",
  warnings: ["one tick wide coverage", "settlement unknown"],
  gaps: null,
  settlement_known: false,
  settlement_value: null,
  data_version: "dv_test",
};

const railSizing: NonNullable<PreflightResponse["sizing"]> = {
  reference_price: 0.7,
  stake_usd: 100,
  contracts: 142.86,
  notional_usd: 100,
  max_loss_usd: 100,
  max_payout_usd: 142.86,
  max_profit_usd: 42.86,
};

function stubRailApi(sizing: PreflightResponse["sizing"] = railSizing) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/strategies")) {
      return jsonResponse({
        builtins: [
          {
            id: "nautilus_ema_cross",
            label: "EMA cross",
            description: "EMA cross",
            direction: "long",
            parameters: [],
          },
        ],
      });
    }
    if (url.endsWith("/api/execution/preflight") && init?.method === "POST") {
      return jsonResponse({
        requested: "conservative",
        effective: "conservative",
        cost_source: "fixed",
        fee_bps: 0,
        slippage_bps: 10,
        min_abs_slippage: 0.001,
        cost_estimate: { fee_bps: 0, slippage_bps: 10, per_side_bps: 10 },
        book_coverage: null,
        warnings: [],
        sizing,
      });
    }
    throw new Error(`Unexpected request in test: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function railProps(overrides: Partial<ComponentProps<typeof StrategyRail>> = {}) {
  return {
    instrumentId,
    barType: "5m",
    from: "2024-09-01",
    to: "2024-12-01",
    indicators: [],
    specs: [],
    builtinId: "nautilus_ema_cross",
    onBuiltinId: vi.fn(),
    strategyJson: null,
    onStrategyJson: vi.fn(),
    profile: "conservative" as const,
    onProfile: vi.fn(),
    quality: railQuality,
    qualityLoading: false,
    onRun: vi.fn(),
    submitting: false,
    submitError: null,
    runId: null,
    ...overrides,
  };
}

describe("StrategyRail warnings and sizing", () => {
  it("acknowledges warnings, then edits a field without a temporal-dead-zone crash", async () => {
    stubRailApi();
    window.localStorage.clear();
    const onRun = vi.fn();
    const user = userEvent.setup();
    render(<StrategyRail {...railProps({ onRun })} />);

    const acknowledge = await screen.findByLabelText(/I acknowledge these data warnings/);
    await user.click(acknowledge);
    // A fee edit re-runs the config memo, which reads `warnings` only when
    // they are acknowledged; a late declaration used to throw here.
    fireEvent.change(screen.getByLabelText("Fee in basis points"), { target: { value: "7" } });

    const run = screen.getByRole("button", { name: "Run backtest" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    expect(onRun).toHaveBeenCalledTimes(1);
    const submitted = onRun.mock.calls[0][0] as BacktestConfig;
    expect(submitted.acknowledged_warnings).toEqual([
      "one tick wide coverage",
      "settlement unknown",
    ]);
    expect(submitted.execution.fee_bps).toBe(7);
  });

  it("shows the preflight sizing preview before the run", async () => {
    stubRailApi();
    window.localStorage.clear();
    render(<StrategyRail {...railProps()} />);

    expect(await screen.findByText("Contracts", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText("Stake")).toBeInTheDocument();
    expect(screen.getByText("Max loss")).toBeInTheDocument();
    expect(screen.getByText("Max payout")).toBeInTheDocument();
    expect(screen.getByText("Max profit")).toBeInTheDocument();
    expect(screen.getByText("142.86")).toBeInTheDocument();
    expect(screen.getAllByText("$100.00")).toHaveLength(2);
    expect(screen.getByText("$142.86")).toBeInTheDocument();
    expect(screen.getByText("$42.86")).toBeInTheDocument();
  });

  it("sends the selected indicators to the preflight endpoint", async () => {
    const fetchMock = stubRailApi();
    window.localStorage.clear();
    const indicators = [{ instance_id: "ema_fast", type: "ema", params: { period: 12 } }];
    render(<StrategyRail {...railProps({ indicators })} />);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/execution/preflight")),
      ).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/execution/preflight"),
    );
    const body = JSON.parse(String((call?.[1] as RequestInit | undefined)?.body)) as {
      indicators?: unknown;
    };
    expect(body.indicators).toEqual(indicators);
  });

  it("requires a fresh acknowledgement when a new warning appears", async () => {
    stubRailApi();
    window.localStorage.clear();
    const user = userEvent.setup();
    const { rerender } = render(<StrategyRail {...railProps()} />);

    const acknowledge = (await screen.findByLabelText(
      /I acknowledge these data warnings/,
    )) as HTMLInputElement;
    await user.click(acknowledge);
    expect(acknowledge).toBeChecked();

    rerender(
      <StrategyRail
        {...railProps({
          quality: { ...railQuality, warnings: [...railQuality.warnings, "insufficient warmup"] },
        })}
      />,
    );
    await waitFor(() => expect(acknowledge).not.toBeChecked());
  });
});
