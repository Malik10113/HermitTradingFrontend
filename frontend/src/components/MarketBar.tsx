import { useMemo } from "react";
import type { Instrument, InstrumentQuality } from "../api/types";
import { instrumentLabel, tokenOf } from "../api/normalise";
import { fmtDate, fmtInteger } from "../lib/format";
import { ErrorState, GradeBadge } from "./ui";

interface MarketBarProps {
  instruments: Instrument[];
  instrumentsLoading: boolean;
  instrumentsError: unknown | null;
  onReloadInstruments: () => void;
  selected: Instrument | null;
  selectedId: string;
  onSelect: (instrumentId: string) => void;
  league: string;
  onLeague: (league: string) => void;
  onOpenPalette: () => void;
  quality: InstrumentQuality | null;
  qualityLoading: boolean;
}

function selectionOf(instrument: Instrument | null): string | null {
  return tokenOf(instrument);
}

export function MarketBar({
  instruments,
  instrumentsLoading,
  instrumentsError,
  onReloadInstruments,
  selected,
  selectedId,
  onSelect,
  league,
  onLeague,
  onOpenPalette,
  quality,
  qualityLoading,
}: MarketBarProps) {
  const leagues = useMemo(
    () =>
      Array.from(
        new Set(
          instruments
            .map((instrument) => instrument.league)
            .filter((value): value is string => !!value),
        ),
      ).sort(),
    [instruments],
  );

  const filtered = useMemo(
    () =>
      league
        ? instruments.filter((instrument) => instrument.league === league)
        : instruments,
    [instruments, league],
  );

  const selection = selectionOf(selected);
  const from = quality?.available_from ?? selected?.coverage?.from ?? null;
  const to = quality?.available_to ?? selected?.coverage?.to ?? null;
  const trades = quality?.trade_count ?? selected?.trade_count ?? null;
  const grade = quality?.quality_grade ?? selected?.quality_grade ?? null;
  const warnings = (quality?.warnings ?? []).map((warning: unknown) =>
    typeof warning === "string"
      ? warning
      : (warning as { message?: string; code?: string })?.message ||
        (warning as { code?: string })?.code ||
        "data warning",
  );

  return (
    <section className="border-b border-line bg-surface-raised" aria-label="Market bar">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="btn min-w-[260px] justify-between text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          onClick={onOpenPalette}
          aria-haspopup="dialog"
          aria-label="Search instruments (Ctrl+K)"
        >
          <span className="flex min-w-0 items-center gap-2">
            <span aria-hidden="true" className="text-ink-faint">
              ⌕
            </span>
            <span className="truncate">
              {selected ? instrumentLabel(selected) : "Search instruments…"}
            </span>
          </span>
          <kbd className="numeric rounded border border-line px-1 text-[9px] text-ink-faint">
            Ctrl K
          </kbd>
        </button>

        <label className="flex items-center gap-1.5">
          <span className="label">League</span>
          <select
            className="input w-auto py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            value={league}
            onChange={(event) => onLeague(event.target.value)}
            aria-label="Filter instruments by league"
          >
            <option value="">All leagues</option>
            {leagues.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label className="flex min-w-[220px] flex-1 items-center gap-1.5">
          <span className="label">Instrument</span>
          <select
            className="input py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            value={selectedId}
            onChange={(event) => onSelect(event.target.value)}
            aria-label="Selected instrument"
            disabled={instrumentsLoading || filtered.length === 0}
          >
            <option value="">
              {instrumentsLoading ? "Loading instruments…" : "Select an instrument"}
            </option>
            {selectedId && !filtered.some((item) => item.instrument_id === selectedId) ? (
              // Keep the remembered selection visible even when the league
              // filter excludes it; the chart should not disagree with the bar.
              <option value={selectedId}>
                {selected ? instrumentLabel(selected) : selectedId} · outside league filter
              </option>
            ) : null}
            {filtered.map((instrument) => (
              <option key={instrument.instrument_id} value={instrument.instrument_id}>
                {instrumentLabel(instrument)}
              </option>
            ))}
          </select>
        </label>

        {selection ? (
          <span
            className={selection === "NO" ? "badge-warn" : "badge-positive"}
            title="YES and NO are separate outcome tokens; each is its own instrument"
          >
            {selection} token
          </span>
        ) : null}

        <span
          className={from && to ? "badge-accent" : "badge"}
          title={
            from && to
              ? `Available bar coverage ${fmtDate(from)} to ${fmtDate(to)}`
              : "Coverage unknown"
          }
        >
          {from && to ? `${fmtDate(from)} → ${fmtDate(to)}` : "coverage not available"}
        </span>

        <span className="badge" title="Fills in the available window">
          {trades != null ? `${fmtInteger(trades)} fills` : "fills not available"}
        </span>

        {qualityLoading ? <span className="badge">quality…</span> : <GradeBadge grade={grade} />}

        {warnings.length > 0 ? (
          <span className="badge-warn" title={warnings.join(" · ")}>
            {warnings.length} data warning{warnings.length === 1 ? "" : "s"}
          </span>
        ) : null}

        {selected ? (
          <span
            className={
              selected.settlement_known === true && selected.settlement_value != null
                ? selected.settlement_value === 1
                  ? "badge-positive"
                  : "badge"
                : "badge-warn"
            }
            data-testid="settlement-badge"
            title="Settlement comes from the mapped results table, never from a price"
          >
            {selected.settlement_known === true && selected.settlement_value != null
              ? `settles ${selected.settlement_value}`
              : selected.settlement_known === false
                ? "settlement unknown"
                : selected.settlement_known === true
                  ? "settlement value not available"
                  : "settlement not available"}
          </span>
        ) : null}
      </div>

      {instrumentsError ? (
        <ErrorState
          error={instrumentsError}
          title="Instruments could not be loaded"
          onRetry={onReloadInstruments}
        />
      ) : !instrumentsLoading && instruments.length === 0 ? (
        <div className="border-t border-line px-3 py-2 text-[12px] text-ink-muted">
          No instruments are registered. The data register and catalog must be built before this
          page can show markets. Nothing is inferred or fabricated.
        </div>
      ) : null}

      {selected ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-3 py-1 text-[10px] text-ink-faint">
          <span className="numeric">{selected.instrument_id}</span>
          {selected.league ? <span>league {selected.league}</span> : null}
          {selected.match_key ? <span className="numeric">match {selected.match_key}</span> : null}
          {selected.end_date ? <span>market end {fmtDate(selected.end_date)}</span> : null}
          {selected.price_tick != null ? (
            <span className="numeric">tick {selected.price_tick}</span>
          ) : (
            <span>tick not available</span>
          )}
          <span>venue {selected.venue ?? "not available"}</span>
        </div>
      ) : null}
    </section>
  );
}
