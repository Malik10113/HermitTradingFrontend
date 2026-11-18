import { useEffect, useState } from "react";
import type { BacktestConfig, RunDetail } from "../../api/types";
import { resolveExecution } from "../../lib/execution";
import { fmtInteger, fmtTimestamp } from "../../lib/format";
import {
  CopyButton,
  ErrorState,
  FallbackBadge,
  KeyValue,
  LoadingState,
  ProfileBadge,
  StatusPill,
} from "../ui";
import { EquityChart } from "./EquityChart";
import { HoldoutPanel } from "./HoldoutPanel";
import { MetricsGrid } from "./MetricsGrid";
import { ComparePanel } from "./ComparePanel";
import { CostsPanel, DiagnosticsPanel, PhasePanel } from "./ResultsPanels";
import { TradeInspector, TradeTable } from "./TradeTable";

type Tab = "overview" | "trades" | "costs" | "diagnostics" | "phases" | "compare";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "trades", label: "Trades" },
  { id: "costs", label: "Costs" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "phases", label: "Phases" },
  { id: "compare", label: "Compare" },
];

interface ResultsDrawerProps {
  runId: string | null;
  run: RunDetail | null;
  loading: boolean;
  error: unknown | null;
  refresh: () => void;
  reveal: () => void;
  revealing: boolean;
  revealError: unknown | null;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  compareIds: string[];
  onCompareChange: (ids: string[]) => void;
  focusTradeId?: string | null;
}

export function ResultsDrawer({
  runId,
  run,
  loading,
  error,
  refresh,
  reveal,
  revealing,
  revealError,
  open,
  onOpen,
  onClose,
  compareIds,
  onCompareChange,
  focusTradeId = null,
}: ResultsDrawerProps) {
  const [tab, setTab] = useState<Tab>(compareIds.length >= 2 ? "compare" : "overview");
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);

  useEffect(() => {
    setSelectedTradeId(null);
    setTab(compareIds.length >= 2 ? "compare" : "overview");
    // compareIds is intentionally read only on a new run selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (focusTradeId) {
      setSelectedTradeId(focusTradeId);
      setTab("trades");
    }
  }, [focusTradeId]);

  if (!runId) return null;

  const execution = run ? resolveExecution(run) : { requested: null, effective: null, reason: null };
  const holdout = run?.holdout ?? null;
  const selectedTrade =
    run?.trades?.find((trade) => trade.trade_id === selectedTradeId) ?? null;
  const progress =
    run?.progress != null ? `${Math.round(run.progress <= 1 ? run.progress * 100 : run.progress)}%` : null;

  if (!open) {
    return (
      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface-raised px-3 py-1.5 shadow-lg">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-ink">Results</span>
          {run ? <StatusPill status={run.status} /> : null}
          {run ? <ProfileBadge profile={execution.effective} prefix="Net" /> : null}
          <span className="numeric text-[10px] text-ink-faint" title={runId}>
            {runId.length > 26 ? `${runId.slice(0, 26)}…` : runId}
          </span>
          {run ? (
            <span className="numeric text-[10px] text-ink-muted">
              {run.metrics?.trade_count != null
                ? `${fmtInteger(run.metrics.trade_count)} trades`
                : "metrics not available"}
            </span>
          ) : null}
          <button type="button" className="btn ml-auto" onClick={onOpen}>
            Open results
          </button>
        </div>
      </div>
    );
  }

  return (
    <section
      className="fixed inset-x-0 bottom-0 z-40 flex max-h-[62vh] min-h-[280px] flex-col border-t border-line-strong bg-surface-raised shadow-2xl"
      aria-label="Backtest results"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink">Results</h2>
        {run ? <StatusPill status={run.status} /> : <span className="badge">loading</span>}
        <span className="numeric text-[10px] text-ink-faint" title={runId}>
          {runId.length > 34 ? `${runId.slice(0, 34)}…` : runId}
        </span>
        <CopyButton value={runId} label="Copy id" />
        {execution.effective ? (
          <ProfileBadge profile={execution.effective} prefix="Net" />
        ) : null}
        <FallbackBadge
          requested={execution.requested}
          effective={execution.effective}
          reason={execution.reason}
        />
        {run?.holdout ? (
          <span
            className={
              run.holdout.revealed || run.holdout.reveal_count > 0 ? "badge-warn" : "badge"
            }
          >
            holdout{" "}
            {run.holdout.revealed || run.holdout.reveal_count > 0
              ? `revealed ${fmtInteger(run.holdout.reveal_count)}×`
              : "hidden"}
          </span>
        ) : null}
        {progress ? <span className="badge-accent">{progress}</span> : null}
        <span className="ml-auto flex items-center gap-1.5">
          <a className="btn-ghost text-[11px]" href={`/runs?run=${encodeURIComponent(runId)}`}>
            Open in Runs
          </a>
          <button type="button" className="btn" onClick={onClose}>
            Collapse
          </button>
        </span>
      </header>

      <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-line px-2" role="tablist" aria-label="Result sections">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            aria-controls={`results-panel-${item.id}`}
            id={`results-tab-${item.id}`}
            className={`tab ${tab === item.id ? "tab-active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
            {item.id === "trades" && run?.trades ? (
              <span className="numeric ml-1 text-[9px] text-ink-faint">{run.trades.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      <div
        className="min-h-0 flex-1 overflow-y-auto"
        role="tabpanel"
        id={`results-panel-${tab}`}
        aria-labelledby={`results-tab-${tab}`}
      >
        {loading && !run ? <LoadingState label="Loading run" rows={4} /> : null}
        {error ? <ErrorState error={error} title="Run could not be loaded" onRetry={refresh} /> : null}

        {run ? (
          <>
            {run.status === "failed" ? (
              <div className="m-2 rounded border border-negative/50 p-2 text-[11px] text-negative" role="alert">
                Run failed:{" "}
                {typeof run.error === "string"
                  ? run.error
                  : run.error?.message ?? "no error detail recorded"}
                {run.error && typeof run.error !== "string" && run.error.code ? (
                  <span className="numeric ml-1 text-[10px] text-ink-faint">
                    code {run.error.code}
                  </span>
                ) : null}
              </div>
            ) : null}
            {run.status === "orphaned" ? (
              <p className="m-2 rounded border border-warn/50 p-2 text-[11px] text-warn" role="note">
                This run is orphaned: its data version is no longer present. It is shown, not re-run.
              </p>
            ) : null}
            {run.status === "queued" || run.status === "running" ? (
              <p className="m-2 text-[11px] text-ink-muted" role="status">
                {run.status === "queued"
                  ? "Queued. Progress and logs stream when a worker starts."
                  : "Running. Artifacts appear when the run completes."}
              </p>
            ) : null}

            {tab === "overview" ? (
              <div className="space-y-2 p-2">
                <MetricsGrid metrics={run.metrics} profile={execution.effective} />
                <div className="grid gap-2 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
                  <div className="panel">
                    <div className="panel-title">
                      <span>Equity and drawdown</span>
                      <button type="button" className="btn-ghost text-[10px]" onClick={refresh}>
                        Refresh
                      </button>
                    </div>
                    <EquityChart
                      equity={run.equity}
                      drawdown={run.drawdown}
                      profileLabelText={execution.effective ?? "profile not available"}
                    />
                  </div>
                  <div className="space-y-2">
                    <HoldoutPanel
                      holdout={holdout}
                      onReveal={reveal}
                      revealing={revealing}
                      revealError={revealError}
                    />
                    <div className="panel p-2">
                      <div className="label mb-1">Run provenance</div>
                      <KeyValue
                        columns={1}
                        items={[
                          {
                            label: "Run hash",
                            value: run.run_hash ?? "not available",
                            title: run.run_hash ?? undefined,
                          },
                          {
                            label: "Strategy hash",
                            value: run.strategy_hash ?? "not available",
                            title: run.strategy_hash ?? undefined,
                          },
                          {
                            label: "Data version",
                            value: run.data_version ?? "not available",
                            title: run.data_version ?? undefined,
                          },
                          {
                            label: "Lifecycle version",
                            value: run.lifecycle_version ?? "not available",
                          },
                          { label: "Created", value: fmtTimestamp(run.created_at) },
                          { label: "Completed", value: fmtTimestamp(run.completed_at) },
                          {
                            label: "Requested profile",
                            value: execution.requested ?? "not available",
                          },
                          { label: "Effective profile", value: execution.effective ?? "not available" },
                          { label: "Fallback reason", value: execution.reason ?? "none" },
                        ]}
                      />
                    </div>
                    {run.config ? (
                      <details className="panel p-2">
                        <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                          Stored config
                        </summary>
                        <pre className="numeric mt-1 max-h-48 overflow-auto text-[10px] leading-relaxed text-ink-muted">
                          {JSON.stringify(run.config as BacktestConfig, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                </div>
                {(run.warnings ?? []).length > 0 ? (
                  <div className="rounded border border-warn/40 p-2">
                    <div className="label mb-1">Run warnings</div>
                    <ul className="space-y-0.5">
                      {(run.warnings ?? []).map((warning, index) => (
                        <li key={index} className="text-[10px] text-warn">
                          {typeof warning === "string"
                            ? warning
                            : `${warning.code}: ${warning.message}`}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}

            {tab === "trades" ? (
              <div>
                <TradeTable
                  trades={run.trades}
                  profile={execution.effective}
                  selectedTradeId={selectedTradeId}
                  onSelectTrade={setSelectedTradeId}
                />
                <TradeInspector trade={selectedTrade} onClose={() => setSelectedTradeId(null)} />
              </div>
            ) : null}

            {tab === "costs" ? (
              <CostsPanel
                costs={run.costs}
                execution={run.execution}
                effectiveProfile={execution.effective}
              />
            ) : null}

            {tab === "diagnostics" ? <DiagnosticsPanel diagnostics={run.diagnostics} /> : null}

            {tab === "phases" ? (
              <PhasePanel
                phases={run.phases}
                unknownPhase={run.phase === "unknown" || run.lifecycle == null}
              />
            ) : null}

            {tab === "compare" ? (
              <ComparePanel
                compareIds={compareIds}
                onChange={onCompareChange}
                currentRunId={run.run_id}
              />
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
