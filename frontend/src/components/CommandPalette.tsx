import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { api } from "../api/client";
import type { Instrument, SearchHit } from "../api/types";
import { instrumentLabel } from "../api/normalise";
import { useDebouncedValue } from "../hooks/useApi";
import { fmtDate, fmtInteger } from "../lib/format";
import { EmptyState, ErrorState } from "./ui";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  instruments: Instrument[];
  selectedId?: string;
  onSelect: (instrumentId: string) => void;
}

interface Candidate {
  instrument_id: string;
  label: string;
  league: string | null;
  selection: string | null;
  coverageFrom?: string | null;
  coverageTo?: string | null;
  local: boolean;
}

function fromInstrument(instrument: Instrument): Candidate {
  return {
    instrument_id: instrument.instrument_id,
    label: instrumentLabel(instrument),
    league: instrument.league ?? null,
    selection: instrument.selection ?? null,
    coverageFrom: instrument.coverage?.from ?? null,
    coverageTo: instrument.coverage?.to ?? null,
    local: true,
  };
}

function fromSearchHit(hit: SearchHit): Candidate {
  return {
    instrument_id: hit.instrument_id,
    label: hit.label ?? hit.instrument_id,
    league: hit.league ?? null,
    selection: hit.selection ?? null,
    coverageFrom: hit.coverage?.from ?? null,
    coverageTo: hit.coverage?.to ?? null,
    local: false,
  };
}

/**
 * Ctrl+K instrument palette. Local filtering is instant; a debounced server
 * search widens the result set without a request per keystroke.
 */
export function CommandPalette({
  open,
  onClose,
  instruments,
  selectedId,
  onSelect,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<Candidate[]>([]);
  const [searchError, setSearchError] = useState<unknown | null>(null);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const debounced = useDebouncedValue(query, 220);

  const local = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const source = instruments.map(fromInstrument);
    if (!needle) return source.slice(0, 40);
    return source
      .filter((candidate) =>
        [candidate.label, candidate.instrument_id, candidate.league]
          .filter(Boolean)
          .some((field) => field!.toLowerCase().includes(needle)),
      )
      .slice(0, 40);
  }, [instruments, query]);

  const results = useMemo(() => {
    const seen = new Set(local.map((candidate) => candidate.instrument_id));
    return [...local, ...remote.filter((candidate) => !seen.has(candidate.instrument_id))].slice(
      0,
      50,
    );
  }, [local, remote]);

  useEffect(() => {
    if (open) {
      setActive(0);
      const handle = window.setTimeout(() => inputRef.current?.focus(), 10);
      return () => window.clearTimeout(handle);
    }
    setQuery("");
    setRemote([]);
    setSearchError(null);
  }, [open]);

  // Keep the keyboard-highlighted option inside the scroll viewport.
  useEffect(() => {
    if (!open) return;
    const activeResult = results[active];
    if (!activeResult) return;
    document
      .getElementById(`command-result-${activeResult.instrument_id}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open, results]);

  useEffect(() => {
    if (!open) return;
    const trimmed = debounced.trim();
    if (trimmed.length < 2) {
      setRemote([]);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    api
      .search(trimmed, controller.signal)
      .then((response) => {
        setRemote((response.results ?? []).map(fromSearchHit));
        setSearchError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setSearchError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setSearching(false);
      });
    return () => controller.abort();
  }, [debounced, open]);

  if (!open) return null;

  const choose = (candidate: Candidate) => {
    onSelect(candidate.instrument_id);
    onClose();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActive(Math.max(results.length - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const candidate = results[active];
      if (candidate) choose(candidate);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/45 p-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search instruments"
        className="panel flex max-h-[70vh] w-full max-w-2xl flex-col overflow-hidden"
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <span aria-hidden="true" className="text-ink-faint">
            ⌕
          </span>
          <input
            ref={inputRef}
            className="w-full bg-transparent text-[14px] text-ink placeholder:text-ink-faint focus:outline-none"
            placeholder="Search team, market, league or instrument id…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            aria-label="Instrument search"
            aria-controls="command-results"
            aria-activedescendant={
              results[active] ? `command-result-${results[active].instrument_id}` : undefined
            }
            role="combobox"
            aria-expanded="true"
            autoComplete="off"
          />
          {searching ? <span className="badge">searching</span> : null}
          <span className="badge">Esc</span>
        </div>
        <div id="command-results" role="listbox" className="min-h-0 flex-1 overflow-y-auto">
          {searchError ? (
            <ErrorState error={searchError} title="Server search unavailable" />
          ) : results.length === 0 ? (
            <EmptyState
              title="No matching instruments"
              message="Try a shorter query, a team name, or a league. Instruments are listed only when they exist in the acquired data."
            />
          ) : (
            results.map((candidate, index) => (
              <button
                key={candidate.instrument_id}
                id={`command-result-${candidate.instrument_id}`}
                type="button"
                role="option"
                aria-selected={index === active}
                className={`flex w-full items-center gap-3 border-b border-line/60 px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  index === active ? "bg-surface-sunken" : "hover:bg-surface-sunken"
                }`}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(candidate)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-ink">{candidate.label}</span>
                  <span className="numeric block truncate text-[10px] text-ink-faint">
                    {candidate.instrument_id}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  {candidate.league ? <span className="badge">{candidate.league}</span> : null}
                  {candidate.selection ? (
                    <span className="badge-accent">{candidate.selection}</span>
                  ) : null}
                  {candidate.coverageFrom && candidate.coverageTo ? (
                    <span className="numeric text-[10px] text-ink-faint">
                      {fmtDate(candidate.coverageFrom)} → {fmtDate(candidate.coverageTo)}
                    </span>
                  ) : null}
                  {candidate.instrument_id === selectedId ? (
                    <span className="badge-positive">selected</span>
                  ) : null}
                </span>
              </button>
            ))
          )}
        </div>
        <div className="flex items-center gap-3 border-t border-line px-3 py-1.5 text-[10px] text-ink-faint">
          <span role="status" aria-live="polite">{fmtInteger(results.length)} shown</span>
          <span>↑↓ navigate · Home/End · Enter select · Esc close</span>
          <span className="ml-auto">One instrument per run in v1</span>
        </div>
      </div>
    </div>
  );
}
