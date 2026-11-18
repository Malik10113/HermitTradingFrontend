import type { CostBreakdown, Diagnostics, EffectiveExecution, PhaseMetric } from "../../api/types";
import { fmtBps, fmtInteger, fmtNumber, fmtPct, fmtRate, fmtUsd } from "../../lib/format";
import { fallbackWarning, profileLabel } from "../../lib/execution";
import { FallbackBadge, KeyValue, ProfileBadge, SourceNote } from "../ui";

export function CostsPanel({
  costs,
  execution,
  effectiveProfile,
}: {
  costs: CostBreakdown | null | undefined;
  execution: EffectiveExecution | null | undefined;
  effectiveProfile: string | null | undefined;
}) {
  const requested = costs?.requested_profile ?? execution?.requested ?? null;
  const effective = costs?.effective_profile ?? execution?.effective ?? effectiveProfile ?? null;
  const reason = costs?.fallback_reason ?? execution?.fallback_reason ?? null;
  const warning = fallbackWarning(requested, effective, reason);

  if (!costs && !execution) {
    return (
      <p className="p-2 text-[11px] text-ink-muted">
        Cost artifact not available for this run. No cost figure is inferred.
      </p>
    );
  }

  return (
    <div className="space-y-2 p-2" aria-label="Cost breakdown">
      <div className="flex flex-wrap items-center gap-2">
        <ProfileBadge profile={effective} prefix="Effective" />
        <FallbackBadge requested={requested} effective={effective} reason={reason} full />
        <span className="text-[10px] text-ink-faint">
          requested {profileLabel(requested)}
        </span>
      </div>
      <KeyValue
        columns={3}
        items={[
          { label: "Fees", value: fmtUsd(costs?.fees_usd) },
          { label: "Spread", value: fmtUsd(costs?.spread_usd) },
          { label: "Impact", value: fmtUsd(costs?.impact_usd) },
          { label: "Total cost", value: fmtUsd(costs?.total_cost_usd) },
          { label: "Total cost", value: fmtBps(costs?.total_cost_bps) },
          {
            label: "Fallback rate",
            value:
              costs?.fallback_rate != null
                ? fmtRate(costs.fallback_rate, 1)
                : "not available",
          },
          {
            label: "Requested profile",
            value: profileLabel(requested),
          },
          {
            label: "Effective profile",
            value: profileLabel(effective),
          },
          {
            label: "Book coverage",
            value:
              execution?.book_coverage?.from && execution?.book_coverage?.to
                ? `${execution.book_coverage.from} → ${execution.book_coverage.to}`
                : "not available",
          },
        ]}
      />
      {warning ? (
        <p className="text-[10px] leading-relaxed text-warn" role="note">
          {warning}
        </p>
      ) : null}
      <SourceNote>
        Slippage is embedded in fill prices; fees are a separate cash line. Cost source is recorded
        in the manifest. Gross and net are shown together in the metrics panel; net is not shown
        without this profile badge.
      </SourceNote>
    </div>
  );
}

function humanise(key: string): string {
  return key.replaceAll("_", " ");
}

function diagnosticValue(key: string, value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (key.endsWith("_pct")) return fmtPct(value, 1);
    if (key.endsWith("_s")) {
      const seconds = value;
      if (seconds < 60) return `${seconds.toFixed(1)} s`;
      return `${(seconds / 60).toFixed(1)} min`;
    }
    if (key.includes("price") || key.includes("probability")) return fmtNumber(value, 3);
    return Number.isInteger(value) ? fmtInteger(value) : fmtNumber(value, 2);
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === null || value === undefined || value === "") return "not available";
  return String(value);
}

export function DiagnosticsPanel({ diagnostics }: { diagnostics: Diagnostics | null | undefined }) {
  if (!diagnostics) {
    return (
      <p className="p-2 text-[11px] text-ink-muted">
        Diagnostics artifact not available for this run.
      </p>
    );
  }
  const entries = Object.entries(diagnostics).filter(
    ([key]) => key !== "leaf_truth_counts" && key !== "warnings",
  ) as Array<[string, unknown]>;
  const leafCounts = diagnostics.leaf_truth_counts ?? null;
  const warnings = diagnostics.warnings ?? [];

  return (
    <div className="space-y-3 p-2">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 md:grid-cols-4">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt className="label">{humanise(key)}</dt>
            <dd className="numeric text-[12px] text-ink">{diagnosticValue(key, value)}</dd>
          </div>
        ))}
      </dl>

      {warnings.length > 0 ? (
        <div>
          <h4 className="label mb-1">Warnings</h4>
          <ul className="space-y-0.5">
            {warnings.map((warning, index) => (
              <li key={index} className="text-[10px] text-warn">
                {typeof warning === "string" ? warning : `${warning.code}: ${warning.message}`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {leafCounts && Object.keys(leafCounts).length > 0 ? (
        <div>
          <h4 className="label mb-1">Per-leaf condition truth counts</h4>
          <div className="max-h-48 overflow-auto rounded border border-line">
            <table className="w-full border-collapse text-[10px]">
              <thead className="sticky top-0 bg-surface-raised">
                <tr>
                  <th scope="col" className="table-head px-2 py-1">Leaf</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">True count</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(leafCounts)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([leaf, count]) => (
                    <tr key={leaf} className="border-t border-line/60">
                      <td className="numeric px-2 py-0.5 text-ink-muted">{leaf}</td>
                      <td className="numeric px-2 py-0.5 text-right">{fmtInteger(count)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <SourceNote>
        Diagnostics are read from the run artifact. A zero count is measured, not missing data.
      </SourceNote>
    </div>
  );
}

export function PhasePanel({
  phases,
  unknownPhase,
}: {
  phases: PhaseMetric[] | null | undefined;
  unknownPhase?: boolean;
}) {
  if (!phases || phases.length === 0) {
    return (
      <p className="p-2 text-[11px] text-ink-muted">
        Phase breakdown not available.{" "}
        {unknownPhase
          ? "The lifecycle row is missing, so the symbol is reported as phase unknown and the phase filter is disabled."
          : "No lifecycle-attributed trades to report."}
      </p>
    );
  }
  return (
    <div className="space-y-2 p-2">
      <table className="w-full border-collapse text-[11px]">
        <caption className="sr-only">Trade breakdown by observed lifecycle phase</caption>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="table-head px-2 py-1">Phase</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Trades</th>
            <th scope="col" className="table-head px-2 py-1 text-right">P&amp;L $</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Return %</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Sharpe</th>
          </tr>
        </thead>
        <tbody>
          {phases.map((phase) => (
            <tr key={phase.phase} className="border-b border-line/60">
              <td className="px-2 py-1 text-ink">
                {phase.phase}
                {phase.phase === "unknown" ? (
                  <span className="ml-1 badge-warn" title="No verified lifecycle row">
                    unknown
                  </span>
                ) : null}
              </td>
              <td className="numeric px-2 py-1 text-right">{fmtInteger(phase.trades)}</td>
              <td className="numeric px-2 py-1 text-right">
                {fmtUsd(phase.pnl_usd, { signed: true })}
              </td>
              <td className="numeric px-2 py-1 text-right">
                {fmtPct(phase.return_pct, 2, { signed: true })}
              </td>
              <td className="numeric px-2 py-1 text-right">{fmtNumber(phase.sharpe, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SourceNote>
        Phases come only from observed ESPN kickoff and end-regular-time wallclocks. Unknown rows
        are never folded into pre-match, in-play or post-match, and no nominal duration is used.
      </SourceNote>
    </div>
  );
}
