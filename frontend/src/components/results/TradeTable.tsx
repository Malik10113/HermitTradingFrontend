import type { Trade } from "../../api/types";
import {
  fmtContracts,
  fmtDuration,
  fmtNumber,
  fmtProbability,
  fmtTimestamp,
  fmtUsd,
  NOT_AVAILABLE,
} from "../../lib/format";
import { EmptyState, KeyValue, ProfileBadge, SourceNote } from "../ui";

const INTENT_LABELS: Record<string, string> = {
  open_long: "open long",
  close_long: "close long",
  open_short: "open short (NO)",
  close_short: "close short (NO)",
};

function sideClass(side: string): string {
  return side === "SELL" ? "text-negative" : "text-positive";
}

export function TradeTable({
  trades,
  profile,
  selectedTradeId,
  onSelectTrade,
}: {
  trades: Trade[] | null | undefined;
  profile: string | null | undefined;
  selectedTradeId: string | null;
  onSelectTrade: (tradeId: string | null) => void;
}) {
  if (!trades || trades.length === 0) {
    return (
      <EmptyState
        title="No trades in this run"
        message="The strategy produced no completed trades. Zero signals is a valid result; it is not replaced with a placeholder trade."
      />
    );
  }

  return (
    <div className="overflow-auto">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-2 py-1 text-[10px] text-ink-faint">
        <span>{trades.length.toLocaleString("en-GB")} completed trade(s)</span>
        <ProfileBadge profile={profile} prefix="Net" />
        <span>Side and token are the actual order intent; a complement buy is never an exit.</span>
      </div>
      <table className="w-full border-collapse text-[11px]">
        <caption className="sr-only">Completed trades with gross and net P&amp;L</caption>
        <thead className="sticky top-0 bg-surface-raised">
          <tr className="border-b border-line">
            <th scope="col" className="table-head px-2 py-1">Trade</th>
            <th scope="col" className="table-head px-2 py-1">Entry (UTC)</th>
            <th scope="col" className="table-head px-2 py-1">Side · token</th>
            <th scope="col" className="table-head px-2 py-1">Intent</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Entry</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Exit</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Contracts</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Gross $</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Net $</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Fees $</th>
            <th scope="col" className="table-head px-2 py-1">Source</th>
            <th scope="col" className="table-head px-2 py-1">Phase</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => {
            const selected = trade.trade_id === selectedTradeId;
            const fallbackSource =
              trade.execution_source === "conservative_fallback" || !!trade.fallback_reason;
            return (
              <tr
                key={trade.trade_id}
                className={`cursor-pointer border-b border-line/60 ${
                  selected ? "bg-surface-sunken" : "hover:bg-surface-sunken/60"
                }`}
                aria-selected={selected}
                onClick={() => onSelectTrade(selected ? null : trade.trade_id)}
              >
                <td className="px-2 py-1">
                  <button
                    type="button"
                    className="numeric text-[10px] text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelectTrade(selected ? null : trade.trade_id);
                    }}
                    aria-expanded={selected}
                    title={trade.trade_id}
                  >
                    {trade.trade_id.length > 14 ? `${trade.trade_id.slice(0, 10)}…` : trade.trade_id}
                  </button>
                </td>
                <td className="numeric whitespace-nowrap px-2 py-1 text-ink-muted">
                  {fmtTimestamp(trade.entry_ts, false)}
                </td>
                <td className="numeric whitespace-nowrap px-2 py-1">
                  <span className={sideClass(trade.side)}>{trade.side}</span>{" "}
                  <span className="text-ink-muted">{trade.token}</span>
                  {trade.close_side ? (
                    <span className="text-ink-faint">
                      {" "}
                      → <span className={sideClass(trade.close_side)}>{trade.close_side}</span>{" "}
                      {trade.token}
                    </span>
                  ) : null}
                </td>
                <td className="px-2 py-1 text-ink-muted">
                  {trade.intent ? INTENT_LABELS[trade.intent] ?? trade.intent : NOT_AVAILABLE}
                </td>
                <td className="numeric px-2 py-1 text-right">{fmtProbability(trade.entry_price)}</td>
                <td className="numeric px-2 py-1 text-right">
                  {trade.held_to_settlement ? (
                    <span className="badge-positive">settle</span>
                  ) : (
                    fmtProbability(trade.exit_price)
                  )}
                </td>
                <td className="numeric px-2 py-1 text-right">{fmtNumber(trade.contracts, 1)}</td>
                <td className="numeric px-2 py-1 text-right">
                  {fmtUsd(trade.gross_pnl_usd, { signed: true })}
                </td>
                <td
                  className={`numeric px-2 py-1 text-right ${
                    (trade.net_pnl_usd ?? 0) < 0 ? "text-negative" : ""
                  }`}
                >
                  {fmtUsd(trade.net_pnl_usd, { signed: true })}
                </td>
                <td className="numeric px-2 py-1 text-right">{fmtUsd(trade.fees_usd)}</td>
                <td className="px-2 py-1">
                  {fallbackSource ? (
                    <span
                      className="badge-warn"
                      title={trade.fallback_reason ?? "Conservative fallback fill"}
                    >
                      fallback
                    </span>
                  ) : (
                    <span className="badge">{trade.execution_source ?? "not available"}</span>
                  )}
                </td>
                <td className="px-2 py-1 text-ink-muted">{trade.phase ?? "unknown"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="px-2 py-1 text-[10px] text-ink-faint">
        Held-to-settlement exits settle to 0 or 1 from the mapped results table, never from a
        price. Fallback rows were priced at Conservative costs.
      </p>
    </div>
  );
}

function contextValue(value: unknown): string {
  if (typeof value === "number") return fmtNumber(value, 4);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value == null) return NOT_AVAILABLE;
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function contextIndicatorRows(
  context: Record<string, unknown>,
): Array<{ label: string; value: string }> {
  const indicators = context.indicator_values;
  if (!indicators || typeof indicators !== "object" || Array.isArray(indicators)) return [];
  const rows: Array<{ label: string; value: string }> = [];
  for (const [instance, values] of Object.entries(indicators as Record<string, unknown>)) {
    if (values && typeof values === "object" && !Array.isArray(values)) {
      for (const [output, value] of Object.entries(values as Record<string, unknown>)) {
        rows.push({ label: `${instance}.${output}`, value: contextValue(value) });
      }
    } else {
      rows.push({ label: instance, value: contextValue(values) });
    }
  }
  return rows;
}

export function TradeInspector({
  trade,
  onClose,
}: {
  trade: Trade | null;
  onClose: () => void;
}) {
  if (!trade) return null;
  const holding =
    trade.entry_ts && trade.exit_ts
      ? (Date.parse(trade.exit_ts) - Date.parse(trade.entry_ts)) / 1000
      : null;
  const context = trade.entry_context ?? null;
  const indicatorRows = context ? contextIndicatorRows(context) : [];
  return (
    <section className="border-t border-line bg-surface-raised p-2" aria-label="Trade inspector">
      <div className="mb-1.5 flex items-center gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink">
          Trade inspector
        </h4>
        <span className="numeric text-[10px] text-ink-faint">{trade.trade_id}</span>
        <button type="button" className="btn-ghost ml-auto" onClick={onClose}>
          Close
        </button>
      </div>
      <KeyValue
        columns={3}
        items={[
          { label: "Entry time", value: fmtTimestamp(trade.entry_ts) },
          {
            label: "Exit time",
            value: trade.held_to_settlement ? "held to settlement" : fmtTimestamp(trade.exit_ts),
          },
          { label: "Holding period", value: fmtDuration(holding) },
          {
            label: "Entry side · token",
            value: `${trade.side} ${trade.token}`,
          },
          {
            label: "Exit side · token",
            value: trade.held_to_settlement
              ? `settlement ${trade.token}`
              : trade.close_side
                ? `${trade.close_side} ${trade.token}`
                : NOT_AVAILABLE,
          },
          {
            label: "Intent",
            value: trade.intent ? (INTENT_LABELS[trade.intent] ?? trade.intent) : NOT_AVAILABLE,
          },
          { label: "Entry price", value: fmtProbability(trade.entry_price) },
          {
            label: "Exit price",
            value: trade.held_to_settlement
              ? "settlement value from results"
              : fmtProbability(trade.exit_price),
          },
          { label: "Contracts", value: fmtContracts(trade.contracts) },
          { label: "Max loss", value: fmtUsd(trade.max_loss_usd) },
          { label: "Max payout", value: fmtUsd(trade.max_payout_usd) },
          { label: "Max profit", value: fmtUsd(trade.max_profit_usd) },
          { label: "Gross P&L", value: fmtUsd(trade.gross_pnl_usd, { signed: true }) },
          { label: "Net P&L", value: fmtUsd(trade.net_pnl_usd, { signed: true }) },
          { label: "Fees", value: fmtUsd(trade.fees_usd) },
          { label: "Slippage", value: fmtUsd(trade.slippage_usd) },
          {
            label: "Entry source",
            value: trade.entry_execution_source ?? NOT_AVAILABLE,
          },
          {
            label: "Exit source",
            value: trade.held_to_settlement
              ? "settlement (no fill)"
              : trade.exit_execution_source ?? NOT_AVAILABLE,
          },
          {
            // The artifact records the reason; fallback is never inferred from
            // the source label alone.
            label: "Entry fallback",
            value: trade.entry_fallback_reason ?? NOT_AVAILABLE,
          },
          {
            label: "Exit fallback",
            value: trade.held_to_settlement
              ? NOT_AVAILABLE
              : trade.exit_fallback_reason ?? NOT_AVAILABLE,
          },
          { label: "Held to settlement", value: trade.held_to_settlement ? "yes" : "no" },
          { label: "Phase", value: trade.phase ?? "unknown" },
        ]}
      />
      {context ? (
        <div className="mt-2">
          <div className="label mb-1">Condition context at entry</div>
          <KeyValue
            columns={2}
            items={[
              {
                label: "Condition",
                value:
                  typeof context.condition === "string"
                    ? context.condition
                    : typeof context.action === "string"
                      ? context.action
                      : NOT_AVAILABLE,
              },
              {
                label: "Signal time",
                value: fmtTimestamp(
                  typeof context.signal_ts === "string" ? context.signal_ts : null,
                ),
              },
              ...indicatorRows,
            ]}
          />
          <details className="mt-1">
            <summary className="cursor-pointer text-[10px] text-ink-faint">
              Raw entry context
            </summary>
            <pre className="numeric mt-1 max-h-32 overflow-auto rounded border border-line bg-surface-sunken p-2 text-[10px] leading-relaxed text-ink-muted">
              {JSON.stringify(context, null, 2)}
            </pre>
          </details>
        </div>
      ) : (
        <SourceNote>No condition context artifact is available for this trade.</SourceNote>
      )}
    </section>
  );
}
