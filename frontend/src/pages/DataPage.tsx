import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Instrument } from "../api/types";
import { instrumentLabel, tokenOf } from "../api/normalise";
import { useApiResource, useDebouncedValue } from "../hooks/useApi";
import { fmtDate, fmtDuration, fmtInteger, fmtPct, fmtUsd } from "../lib/format";
import {
  sortInstruments,
  type InstrumentSortDirection,
} from "../lib/instrumentSort";
import { workspaceSearchString, DEFAULT_WORKSPACE_STATE } from "../lib/urlState";
import {
  EmptyState,
  ErrorState,
  GradeBadge,
  KeyValue,
  LoadingState,
  SourceNote,
} from "../components/ui";

export function DataPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const league = searchParams.get("league") ?? "";
  const venue = searchParams.get("venue") ?? "";
  const from = searchParams.get("from") ?? "";
  const to = searchParams.get("to") ?? "";
  const exactDate = searchParams.get("date") ?? "";

  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const debouncedQuery = useDebouncedValue(query, 250);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [knownLeagues, setKnownLeagues] = useState<string[]>([]);
  const [sortDirection, setSortDirection] = useState<InstrumentSortDirection | null>(null);

  const instrumentsResource = useApiResource(
    (signal) =>
      api.instruments(
        {
          q: debouncedQuery.trim() || undefined,
          league: league || undefined,
          venue: venue || undefined,
          date: exactDate || undefined,
          from: from || undefined,
          to: to || undefined,
          limit: 500,
        },
        signal,
      ),
    [debouncedQuery, league, venue, exactDate, from, to],
  );

  const instruments = instrumentsResource.data?.instruments ?? [];
  const total = instrumentsResource.data?.total ?? instruments.length;

  // Sorting is client-side over the rows this page already holds; the server
  // query, its order and the page limit are untouched.
  const rows = useMemo(
    () => sortInstruments(instruments, sortDirection),
    [instruments, sortDirection],
  );

  const cycleSortDirection = () => {
    setSortDirection((current) => (current === null ? "asc" : current === "asc" ? "desc" : "asc"));
  };

  const sortAria: "ascending" | "descending" | "none" =
    sortDirection === "asc" ? "ascending" : sortDirection === "desc" ? "descending" : "none";

  // Accumulate league names so selecting one does not erase the alternatives
  // from the dropdown. The current league is always present.
  useEffect(() => {
    setKnownLeagues((current) => {
      const seen = new Set(current);
      for (const instrument of instruments) {
        if (instrument.league) seen.add(instrument.league);
      }
      if (league) seen.add(league);
      const next = Array.from(seen).sort();
      return next.length === current.length && next.every((item, i) => item === current[i])
        ? current
        : next;
    });
  }, [instruments, league]);

  const leagues = knownLeagues;

  const patchFilters = (patch: Record<string, string>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setSearchParams(next, { replace: true });
  };

  const clearFilters = () => {
    const next = new URLSearchParams();
    if (query) next.set("q", query);
    setSearchParams(next, { replace: true });
  };

  const selected: Instrument | null =
    instruments.find((instrument) => instrument.instrument_id === selectedId) ?? null;

  const qualityResource = useApiResource(
    (signal) => api.quality(selectedId as string, {}, signal),
    [selectedId],
    !!selectedId,
  );

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-3 p-3">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[15px] font-semibold text-ink">Data explorer</h1>
          <p className="text-[11px] text-ink-muted">
            Registered Polymarket outcomes, their coverage and data-quality record. Read-only;
            nothing is repaired or inferred here.
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-end gap-2">
          <label>
            <span className="label">Search</span>
            <input
              className="input mt-1 w-56"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="instrument, team, league…"
              aria-label="Filter data table"
            />
          </label>
          <label>
            <span className="label">Venue</span>
            <select
              className="input mt-1 w-auto"
              value={venue}
              onChange={(event) => patchFilters({ venue: event.target.value })}
              aria-label="Filter by venue"
              title="v1 reads Polymarket only; Kalshi and Betfair are v2"
            >
              <option value="">All venues</option>
              <option value="polymarket">Polymarket</option>
            </select>
          </label>
          <label>
            <span className="label">League</span>
            <select
              className="input mt-1 w-auto"
              value={league}
              onChange={(event) => patchFilters({ league: event.target.value })}
              aria-label="Filter by league"
            >
              <option value="">All leagues</option>
              {leagues.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">Event from</span>
            <input
              className="input mt-1 w-[130px]"
              type="date"
              value={from}
              onChange={(event) => patchFilters({ from: event.target.value })}
              aria-label="Filter from event date"
            />
          </label>
          <label>
            <span className="label">Event to</span>
            <input
              className="input mt-1 w-[130px]"
              type="date"
              value={to}
              onChange={(event) => patchFilters({ to: event.target.value })}
              aria-label="Filter to event date"
            />
          </label>
          {exactDate ? (
            <span className="badge" title="Exact event date from the URL">
              date {exactDate}
              <button
                type="button"
                className="btn-ghost ml-1 px-1 text-[10px]"
                onClick={() => patchFilters({ date: "" })}
                aria-label="Clear exact event date"
              >
                ✕
              </button>
            </span>
          ) : null}
          <button type="button" className="btn" onClick={clearFilters}>
            Clear
          </button>
          <button type="button" className="btn" onClick={instrumentsResource.reload}>
            Refresh
          </button>
        </div>
      </header>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="panel overflow-hidden" aria-label="Instrument inventory">
          <div className="panel-title">
            <span>Instruments</span>
            <span
              className="normal-case tracking-normal text-[10px]"
              title="Page length / true match count for the active server-side filters"
            >
              {fmtInteger(instruments.length)} of {fmtInteger(total)}
            </span>
            {sortDirection ? (
              <span
                className="normal-case tracking-normal text-[10px] text-ink-faint"
                title="Ordering covers only the rows loaded on this page, not the full inventory"
              >
                sorted within loaded rows
              </span>
            ) : null}
          </div>
          {instrumentsResource.loading ? (
            <LoadingState rows={6} label="Loading instruments" />
          ) : instrumentsResource.error ? (
            <ErrorState
              error={instrumentsResource.error}
              title="Instrument inventory unavailable"
              onRetry={instrumentsResource.reload}
            />
          ) : instruments.length === 0 ? (
            <EmptyState
              title="No instruments match"
              message="The catalog is empty or the filters exclude every market. Build the instrument index before searching, or clear the filters."
            />
          ) : (
            <div className="max-h-[65vh] overflow-auto">
              <table className="w-full border-collapse text-[11px]">
                <caption className="sr-only">Registered instruments with coverage and settlement</caption>
                <thead className="sticky top-0 bg-surface-raised">
                  <tr className="border-b border-line">
                    <th scope="col" className="table-head px-2 py-1">Instrument</th>
                    <th scope="col" className="table-head px-2 py-1">Venue</th>
                    <th scope="col" className="table-head px-2 py-1" aria-sort={sortAria}>
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 text-inherit hover:text-ink"
                        onClick={cycleSortDirection}
                        title="Sort the loaded rows by league; null leagues stay last"
                      >
                        League
                        {sortDirection === "asc" ? <span aria-hidden="true">▲</span> : null}
                        {sortDirection === "desc" ? <span aria-hidden="true">▼</span> : null}
                      </button>
                    </th>
                    <th scope="col" className="table-head px-2 py-1">Event date</th>
                    <th scope="col" className="table-head px-2 py-1">Token</th>
                    <th scope="col" className="table-head px-2 py-1">Coverage</th>
                    <th scope="col" className="table-head px-2 py-1 text-right">Fills</th>
                    <th scope="col" className="table-head px-2 py-1">Grade</th>
                    <th scope="col" className="table-head px-2 py-1">Settlement</th>
                    <th scope="col" className="table-head px-2 py-1" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((instrument) => {
                    const selection = tokenOf(instrument);
                    return (
                      <tr
                        key={instrument.instrument_id}
                        className={`border-b border-line/60 ${
                          selectedId === instrument.instrument_id
                            ? "bg-surface-sunken"
                            : "hover:bg-surface-sunken/60"
                        }`}
                      >
                        <td className="max-w-[360px] px-2 py-1">
                          <button
                            type="button"
                            className="block max-w-full truncate text-left text-ink hover:underline"
                            onClick={() => setSelectedId(instrument.instrument_id)}
                            title={instrument.instrument_id}
                          >
                            {instrumentLabel(instrument)}
                          </button>
                          <span className="numeric block truncate text-[9px] text-ink-faint">
                            {instrument.instrument_id}
                          </span>
                        </td>
                        <td className="px-2 py-1 text-ink-muted">
                          {instrument.venue ?? "not available"}
                        </td>
                        <td className="px-2 py-1 text-ink-muted">{instrument.league ?? "not available"}</td>
                        <td className="numeric whitespace-nowrap px-2 py-1 text-[10px] text-ink-muted">
                          {instrument.event_date ? fmtDate(instrument.event_date) : "not available"}
                        </td>
                        <td className="px-2 py-1">
                          <span className={selection === "NO" ? "badge-warn" : "badge-accent"}>
                            {selection ?? "—"}
                          </span>
                        </td>
                        <td className="numeric whitespace-nowrap px-2 py-1 text-[10px] text-ink-muted">
                          {instrument.coverage?.from && instrument.coverage?.to
                            ? `${fmtDate(instrument.coverage.from)} → ${fmtDate(instrument.coverage.to)}`
                            : "not available"}
                        </td>
                        <td className="numeric px-2 py-1 text-right">
                          {fmtInteger(instrument.trade_count)}
                        </td>
                        <td className="px-2 py-1">
                          <GradeBadge grade={instrument.quality_grade} />
                        </td>
                        <td className="px-2 py-1 text-[10px]">
                          {instrument.settlement_known && instrument.settlement_value != null ? (
                            <span className="badge-positive">{instrument.settlement_value}</span>
                          ) : (
                            <span className="badge-warn">unknown</span>
                          )}
                        </td>
                        <td className="px-2 py-1">
                          <Link
                            className="btn-ghost text-[10px]"
                            to={`/?${workspaceSearchString({
                              ...DEFAULT_WORKSPACE_STATE,
                              instrument: instrument.instrument_id,
                              barType: "1m",
                            }).replace(/^\?/, "")}`}
                          >
                            Open
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="border-t border-line px-2 py-1">
            <SourceNote>
              Coverage is the stored bar window; a missing coverage field is shown as not available,
              never estimated. Every token is a separate binary instrument.
            </SourceNote>
          </div>
        </section>

        <section className="panel" aria-label="Instrument quality">
          <div className="panel-title">
            <span>Coverage and quality</span>
            {selectedId ? (
              <span className="normal-case tracking-normal text-[10px] text-ink-faint">
                {selectedId.length > 30 ? `${selectedId.slice(0, 30)}…` : selectedId}
              </span>
            ) : null}
          </div>
          {!selectedId ? (
            <EmptyState
              title="Select an instrument"
              message="Choose a row to inspect coverage, gaps, trade statistics and settlement state."
            />
          ) : qualityResource.loading ? (
            <LoadingState rows={5} label="Loading quality" />
          ) : qualityResource.error ? (
            <ErrorState
              error={qualityResource.error}
              title="Quality record unavailable"
              onRetry={qualityResource.reload}
            />
          ) : qualityResource.data ? (
            <div className="space-y-2 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <GradeBadge grade={qualityResource.data.quality_grade} />
                {(qualityResource.data.warnings ?? []).length > 0 ? (
                  <span className="badge-warn">
                    {qualityResource.data.warnings!.length} warning(s)
                  </span>
                ) : (
                  <span className="badge">no warnings</span>
                )}
              </div>
              <KeyValue
                columns={2}
                items={[
                  {
                    label: "Available period",
                    value:
                      qualityResource.data.available_from && qualityResource.data.available_to
                        ? `${fmtDate(qualityResource.data.available_from)} → ${fmtDate(
                            qualityResource.data.available_to,
                          )}`
                        : "not available",
                  },
                  { label: "Fills", value: fmtInteger(qualityResource.data.trade_count) },
                  {
                    label: "Active minutes",
                    value:
                      qualityResource.data.active_minute_pct != null
                        ? fmtPct(qualityResource.data.active_minute_pct, 1)
                        : "not available",
                  },
                  {
                    label: "Longest gap",
                    value: fmtDuration(qualityResource.data.longest_gap_s),
                  },
                  {
                    label: "Median trade",
                    value: fmtUsd(qualityResource.data.median_trade_usd),
                  },
                  {
                    label: "Settlement",
                    value:
                      qualityResource.data.settlement_known != null
                        ? qualityResource.data.settlement_known
                          ? `${qualityResource.data.settlement_value ?? "not available"}`
                          : "not available"
                        : selected?.settlement_known
                          ? `${selected.settlement_value ?? "not available"}`
                          : "not available",
                  },
                ]}
              />
              {(qualityResource.data.warnings ?? []).length > 0 ? (
                <ul className="space-y-0.5">
                  {(qualityResource.data.warnings ?? []).map((warning) => (
                    <li key={warning} className="text-[10px] text-warn">
                      ⚠ {warning}
                    </li>
                  ))}
                </ul>
              ) : null}

              <div>
                <h3 className="label mb-1">Gaps</h3>
                {(qualityResource.data.gaps ?? []).length === 0 ? (
                  <p className="text-[10px] text-ink-faint">
                    {qualityResource.data.gaps == null
                      ? "Gap list not available in the quality record."
                      : "No gaps reported in the stored window."}
                  </p>
                ) : (
                  <div className="max-h-40 overflow-auto rounded border border-line">
                    <table className="w-full border-collapse text-[10px]">
                      <thead className="sticky top-0 bg-surface-raised">
                        <tr>
                          <th scope="col" className="table-head px-2 py-1">From</th>
                          <th scope="col" className="table-head px-2 py-1">To</th>
                          <th scope="col" className="table-head px-2 py-1 text-right">Duration</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(qualityResource.data.gaps ?? []).map((gap, index) => (
                          <tr key={`${gap.from}:${index}`} className="border-t border-line/60">
                            <td className="numeric px-2 py-0.5">{fmtDate(gap.from)}</td>
                            <td className="numeric px-2 py-0.5">{fmtDate(gap.to)}</td>
                            <td className="numeric px-2 py-0.5 text-right">
                              {fmtDuration(gap.duration_s)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
              <SourceNote>
                A gap is an interval without a bar, not a zero-volume bar. Quality warnings inform
                a run but never block it; acknowledged warnings are recorded in the manifest.
              </SourceNote>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
