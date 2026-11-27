import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { WorkspacePage } from "./WorkspacePage";
import { MetaProvider } from "../state/meta";
import { ThemeProvider } from "../state/theme";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function renderWorkspace() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/meta")) return jsonResponse({ data_version: "sha256:abc" });
      if (url.includes("/api/instruments")) return jsonResponse({ instruments: [], total: 0 });
      if (url.endsWith("/api/indicators")) return jsonResponse([]);
      if (url.endsWith("/api/strategies")) return jsonResponse({ builtins: [], saved: [] });
      throw new Error(`Unexpected request ${url}`);
    }),
  );
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <ThemeProvider>
        <MetaProvider>
          <Routes>
            <Route path="/" element={<WorkspacePage />} />
          </Routes>
        </MetaProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("workspace keyboard access", () => {
  it("moves focus across chart-range presets with arrow keys", async () => {
    renderWorkspace();
    const all = await screen.findByRole("button", { name: /set chart range to all/i });
    all.focus();
    expect(all).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: /set chart range to match/i })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: /set chart range to 1M/i })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("button", { name: /set chart range to match/i })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(all).toHaveFocus();
  });

  it("disables phase filters when no observed lifecycle row exists", async () => {
    renderWorkspace();
    await screen.findByRole("button", { name: /set chart range to all/i });
    expect(screen.getByRole("button", { name: "In-play" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pre-match" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "All" })).toBeEnabled();
  });

  it("announces empty instrument inventory instead of inventing markets", async () => {
    renderWorkspace();
    expect(
      await screen.findByText(/No instruments are registered/i),
    ).toBeInTheDocument();
  });
});
