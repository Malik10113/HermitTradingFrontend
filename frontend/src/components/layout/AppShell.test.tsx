import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { MetaProvider } from "../../state/meta";
import { ThemeProvider } from "../../state/theme";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("application shell accessibility", () => {
  it("exposes landmarks, navigation, theme control and provenance versions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/meta")) {
          return jsonResponse({
            data_version: "sha256:abc123def4567890",
            code_version: "git:deadbeef",
            nautilus_version: "2.0.0rc9",
            lifecycle_version: "life:2026",
            generated_at: "2026-11-18T10:00:00Z",
          });
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/"]}>
        <ThemeProvider>
          <MetaProvider>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<div>Workspace body</div>} />
              </Route>
            </Routes>
          </MetaProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /skip to main content/i })).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "Primary" });
    for (const label of ["Workspace", "Data", "Runs", "Presets"]) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute(
        "href",
        label === "Workspace" ? "/" : `/${label.toLowerCase()}`,
      );
    }
    expect(navigation).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("Workspace body");
    expect(screen.getByText("Read-only acquired data", { exact: false })).toBeInTheDocument();

    // The footer is the application's provenance landmark, pinned to the viewport
    // bottom so it stays visible while a tab scrolls (mirrors the sticky header).
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent(/Read-only acquired data/);
    expect(footer).toHaveTextContent(/Meta as of/);
    expect(footer.className).toContain("sticky");
    expect(footer.className).toContain("bottom-0");

    const toggle = screen.getByRole("button", { name: /switch to (light|dark) theme/i });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    await userEvent.click(toggle);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await waitFor(() => {
      expect(screen.getAllByText("sha256:abc12…").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("2.0.0rc9").length).toBeGreaterThan(0);
  });
});
