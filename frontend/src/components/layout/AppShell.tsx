import { NavLink, Outlet } from "react-router-dom";
import { useMeta } from "../../state/meta";
import { useTheme } from "../../state/theme";
import { fmtTimestamp, fmtVersion, NOT_AVAILABLE } from "../../lib/format";
import { VersionChip } from "../ui";

const NAV = [
  { to: "/", label: "Workspace", end: true },
  { to: "/data", label: "Data" },
  { to: "/runs", label: "Runs" },
  { to: "/presets", label: "Presets" },
];

export function AppShell() {
  const { meta, loading, error } = useMeta();
  const { theme, toggle } = useTheme();

  return (
    <div className="flex h-full min-h-screen flex-col bg-surface">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface-raised focus:px-3 focus:py-2 focus:text-ink"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-surface-raised/95 backdrop-blur">
        <div className="flex h-11 items-center gap-4 px-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="numeric text-[12px] font-bold tracking-[0.14em] text-ink">
              HERMIT
            </span>
            <span className="hidden text-[10px] uppercase tracking-[0.16em] text-ink-faint sm:inline">
              research instrument
            </span>
          </div>
          <nav aria-label="Primary" className="flex items-center gap-0.5">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded px-2.5 py-1.5 text-[12px] transition-colors ${
                    isActive
                      ? "bg-surface-sunken font-semibold text-ink"
                      : "text-ink-muted hover:text-ink"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {loading ? (
              <span className="badge">meta loading</span>
            ) : error ? (
              <span className="badge-danger" title="GET /api/meta failed">
                meta unavailable
              </span>
            ) : (
              <span className="hidden items-center gap-3 md:flex">
                <VersionChip label="data" value={meta?.data_version} title={meta?.data_version ?? undefined} />
                <VersionChip label="code" value={meta?.code_version} />
                <VersionChip label="nautilus" value={meta?.nautilus_version} />
              </span>
            )}
            <button
              type="button"
              className="btn-ghost border border-line"
              onClick={toggle}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              aria-pressed={theme === "dark"}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              <span aria-hidden="true">{theme === "dark" ? "☾" : "☀"}</span>
              <span className="hidden sm:inline">{theme === "dark" ? "Dark" : "Light"}</span>
            </button>
          </div>
        </div>
      </header>

      <main id="main" className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>

      <footer className="mt-auto border-t border-line bg-surface-raised px-3 py-1.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-ink-faint">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <VersionChip label="data version" value={meta?.data_version} title={meta?.data_version ?? undefined} />
            <VersionChip label="code version" value={meta?.code_version} title={meta?.code_version ?? undefined} />
            <VersionChip label="nautilus" value={meta?.nautilus_version} title={meta?.nautilus_version ?? undefined} />
            {meta?.lifecycle_version ? (
              <VersionChip label="lifecycle" value={meta.lifecycle_version} title={meta.lifecycle_version} />
            ) : null}
          </span>
          <span className="ml-auto">
            Meta as of {meta?.generated_at ? fmtTimestamp(meta.generated_at) : NOT_AVAILABLE}
          </span>
          <span className="w-full text-ink-faint sm:w-auto">
            Read-only acquired data · historical simulation output only · no live trading ·
            no profitability claim
          </span>
          <span className="numeric hidden text-ink-faint lg:inline">
            {meta?.catalog?.instruments != null
              ? `${meta.catalog.instruments.toLocaleString("en-GB")} instruments · `
              : ""}
            {meta?.data_version ? fmtVersion(meta.data_version) : "data version unavailable"}
          </span>
        </div>
      </footer>
    </div>
  );
}
