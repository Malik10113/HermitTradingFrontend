import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { RunSummary } from "../api/types";
import { useApiResource } from "../hooks/useApi";
import { resolveExecution } from "../lib/execution";
import { fmtDate, fmtInteger, fmtTimestamp, fmtUsd } from "../lib/format";
import { netMetric } from "../components/results/MetricsGrid";
import { SweepsPanel } from "../components/results/SweepsPanel";
import {
  EmptyState,
  ErrorState,
  FallbackBadge,
  LoadingState,
  ProfileBadge,
  StatusPill,
} from "../components/ui";

const STATUSES = ["all", "queued", "running", "complete", "failed", "cancelled", "orphaned"] as const;

/** Compact, copy-safe run identifier for accessible labels and titles. */
function shortHash(value: string, length = 8): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function isActive(status: string): boolean {
  return status === "queued" || status === "running";
}

export function RunsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("all");
  // Status is filtered by the API so `total` is the true matching count, not
  // the length of a client-side slice.
  const runsResource = useApiResource(
    (signal) =>
      api.runs(
        { status: status === "all" ? undefined : status, limit: 200 },
        signal,
      ),
    [status],
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown | null>(null);

  const runs = runsResource.data?.runs ?? [];
  const total = runsResource.data?.total ?? runs.length;

  const removeRun = async (run: RunSummary) => {
    if (deletingId) return;
    setDeletingId(run.run_id);
    setActionError(null);
    try {
      await api.deleteRun(run.run_id);
      setSelected((current) => current.filter((id) => id !== run.run_id));
      runsResource.reload();
    } catch (caught) {
      setActionError(caught);
    } finally {
      setDeletingId(null);
    }
  };

  const toggle = (runId: string) => {
    setSelected((current) =>
      current.includes(runId)
        ? current.filter((id) => id !== runId)
        : current.length >= 3
          ? current
          : [...current, runId],
    );
  };

  const openComparison = () => {
    if (selected.length < 2) return;
    const params = new URLSearchParams();
    params.set("run", selected[0]);
    params.set("compare", selected.join(","));
    navigate(`/?${params.toString()}`);
  };

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-3 p-3">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[15px] font-semibold text-ink">Runs</h1>
          <p className="text-[11px] text-ink-muted">
            Backtest history. A run binds its config, data version, execution profile and
            artifacts; a run whose data version is gone is orphaned, not re-run.
          </p>
        </div>
        <div className="ml-auto flex items-end gap-2">
          <label>
            <span className="label">Status</span>
            <select
              className="input mt-1 w-auto"
              value={status}
              onChange={(event) => setStatus(event.target.value as (typeof STATUSES)[number])}
              aria-label="Filter runs by status"
            >
              {STATUSES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn" onClick={runsResource.reload}>
            Refresh
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <span className="badge" title="True count for the active server-side status filter">
          {fmtInteger(total)} run(s)
        </span>
        {runs.length !== total ? (
          <span className="badge" title="The table shows one page of the run history">
            showing {fmtInteger(runs.length)}
          </span>
        ) : null}
        {selected.map((id) => (
          <span key={id} className="chip">
            <span className="numeric text-[10px]" title={id}>
              {id.length > 22 ? `${id.slice(0, 22)}…` : id}
            </span>
            <button
              type="button"
              className="btn-ghost px-1 text-[10px]"
              onClick={() => toggle(id)}
              aria-label={`Deselect ${id}`}
            >
              ✕
            </button>
          </span>
        ))}
        <button
          type="button"
          className="btn"
          onClick={openComparison}
          disabled={selected.length < 2}
          title="Open the workspace with these runs selected for comparison"
        >
          Compare {selected.length > 0 ? `(${selected.length})` : ""}
        </button>
        {selected.length >= 3 ? (
          <span className="text-[10px] text-ink-faint">Maximum three runs in a comparison.</span>
        ) : null}
        <span className="ml-auto text-[10px] text-ink-faint">
          Select 2–3 runs, then open the comparison overlay.
        </span>
      </div>

      {actionError ? (
        <p className="rounded border border-negative/40 px-2 py-1 text-[11px] text-negative" role="alert">
          Run was not cancelled or deleted: {errorMessage(actionError)}
        </p>
      ) : null}

      <SweepsPanel />

      <section className="panel overflow-hidden" aria-label="Run history">
        {runsResource.loading ? (
          <LoadingState rows={7} label="Loading runs" />
        ) : runsResource.error ? (
          <ErrorState error={runsResource.error} title="Run history unavailable" onRetry={runsResource.reload} />
        ) : runs.length === 0 ? (
          <EmptyState
            title="No runs"
            message="No backtest matches this filter. Runs appear after POST /api/backtests and are stored with their manifest and artifacts."
          />
        ) : (
          <div className="overflow-auto">
            <table className="w-full border-collapse text-[11px]">
              <caption className="sr-only">Backtest run history</caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr className="border-b border-line">
                  <th scope="col" className="table-head px-2 py-1">Compare</th>
                  <th scope="col" className="table-head px-2 py-1">Run</th>
                  <th scope="col" className="table-head px-2 py-1">Status</th>
                  <th scope="col" className="table-head px-2 py-1">Created</th>
                  <th scope="col" className="table-head px-2 py-1">Data version</th>
                  <th scope="col" className="table-head px-2 py-1">Profile</th>
                  <th scope="col" className="table-head px-2 py-1">Holdout</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Net P&amp;L</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Trades</th>
                  <th scope="col" className="table-head px-2 py-1">Run hash</th>
                  <th scope="col" className="table-head px-2 py-1" />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const execution = resolveExecution(run as RunSummary);
                  const net = netMetric(run.metrics, "net_pnl_usd");
                  return (
                    <tr key={run.run_id} className="border-b border-line/60 hover:bg-surface-sunken/50">
                      <td className="px-2 py-1">
                        <input
                          type="checkbox"
                          checked={selected.includes(run.run_id)}
                          onChange={() => toggle(run.run_id)}
                          disabled={!selected.includes(run.run_id) && selected.length >= 3}
                          aria-label={`Select run ${run.run_id} for comparison`}
                        />
                      </td>
                      <td className="px-2 py-1">
                        <span className="numeric block text-[10px] text-ink" title={run.run_id}>
                          {run.run_id.length > 24 ? `${run.run_id.slice(0, 24)}…` : run.run_id}
                        </span>
                        <span className="block text-[9px] text-ink-faint">
                          strategy {run.strategy_hash ? `${run.strategy_hash.slice(0, 12)}…` : "not available"}
                        </span>
                      </td>
                      <td className="px-2 py-1">
                        <StatusPill status={run.status} />
                      </td>
                      <td className="numeric whitespace-nowrap px-2 py-1 text-ink-muted">
                        {fmtDate(run.created_at)}
                      </td>
                      <td className="numeric px-2 py-1 text-[10px] text-ink-muted" title={run.data_version ?? undefined}>
                        {run.data_version ? `${run.data_version.slice(0, 14)}…` : "not available"}
                      </td>
                      <td className="px-2 py-1">
                        <ProfileBadge profile={execution.effective} prefix={null} />
                        <FallbackBadge
                          requested={execution.requested}
                          effective={execution.effective}
                          reason={execution.reason}
                        />
                      </td>
                      <td className="px-2 py-1 text-[10px] text-ink-muted">
                        {run.holdout
                          ? run.holdout.revealed || run.holdout.reveal_count > 0
                            ? `revealed ${fmtInteger(run.holdout.reveal_count)}×`
                            : "hidden"
                          : "not available"}
                      </td>
                      <td className="numeric px-2 py-1 text-right" title="Net includes fees and slippage">
                        {fmtUsd(net, { signed: true })}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtInteger(netMetric(run.metrics, "trade_count"))}
                      </td>
                      <td className="numeric px-2 py-1 text-[9px] text-ink-faint" title={run.run_hash ?? undefined}>
                        {run.run_hash ? `${run.run_hash.slice(0, 10)}…` : "not available"}
                      </td>
                      <td className="px-2 py-1">
                        <div className="flex items-center gap-1.5">
                          <Link className="btn-ghost text-[10px]" to={`/?run=${encodeURIComponent(run.run_id)}`}>
                            Open
                          </Link>
                          <button
                            type="button"
                            className="btn-ghost text-[10px]"
                            onClick={() => void removeRun(run)}
                            disabled={deletingId === run.run_id}
                            aria-label={`${isActive(run.status) ? "Cancel" : "Delete"} run ${shortHash(run.run_id, 8)}`}
                            title={
                              isActive(run.status)
                                ? "Cancel the queued or running job and delete the run"
                                : "Delete this run and its artifacts"
                            }
                          >
                            {deletingId === run.run_id
                              ? isActive(run.status)
                                ? "Cancelling…"
                                : "Deleting…"
                              : isActive(run.status)
                                ? "Cancel"
                                : "Delete"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="border-t border-line px-2 py-1 text-[10px] text-ink-faint">
          Net P&amp;L is shown with its effective execution profile. Out-of-sample results stay
          hidden until an explicit reveal, which is logged in the holdout ledger.
        </div>
      </section>

      <p className="text-[10px] text-ink-faint">
        Run list read from the API · page rendered {fmtTimestamp(new Date().toISOString())}
      </p>
    </div>
  );
}
