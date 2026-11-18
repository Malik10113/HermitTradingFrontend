import { useState } from "react";
import type { HoldoutState } from "../../api/types";
import { errorMessage } from "../../api/client";
import { fmtDate, fmtInteger, fmtTimestamp } from "../../lib/format";
import { SourceNote } from "../ui";

export function HoldoutPanel({
  holdout,
  onReveal,
  revealing,
  revealError,
}: {
  holdout: HoldoutState | null | undefined;
  onReveal: () => void;
  revealing: boolean;
  revealError: unknown | null;
}) {
  const [confirming, setConfirming] = useState(false);

  if (!holdout) {
    return (
      <div className="rounded border border-line p-2 text-[11px] text-ink-muted">
        Holdout state not available for this run. No out-of-sample claim is made.
      </div>
    );
  }

  const revealed = holdout.revealed || holdout.reveal_count > 0;
  const repeat = holdout.reveal_count >= 1;

  return (
    <div className="space-y-1.5 rounded border border-line p-2" aria-label="Holdout ledger">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">Out-of-sample holdout</span>
        <span className={revealed ? "badge-warn" : "badge"}>
          {revealed ? `revealed ${fmtInteger(holdout.reveal_count)}×` : "hidden"}
        </span>
        {holdout.revealed_at ? (
          <span className="text-[10px] text-ink-faint">
            last {fmtTimestamp(holdout.revealed_at)}
          </span>
        ) : null}
      </div>

      {holdout.split ? (
        <p className="numeric text-[10px] text-ink-muted">
          train ends {fmtDate(holdout.split.train_end)} · test starts{" "}
          {fmtDate(holdout.split.test_start)} · embargo{" "}
          {holdout.split.embargo_days != null ? `${holdout.split.embargo_days} days` : "not set"}
        </p>
      ) : (
        <p className="text-[10px] text-ink-faint">Split not available for this run.</p>
      )}

      {!revealed ? (
        <button type="button" className="btn" onClick={onReveal} disabled={revealing}>
          {revealing ? "Revealing…" : "Reveal out-of-sample"}
        </button>
      ) : repeat ? (
        <div className="space-y-1">
          <p className="text-[10px] leading-relaxed text-warn" role="alert">
            This holdout has already been revealed {fmtInteger(holdout.reveal_count)} time(s). Each
            further look adds to the ledger and weakens the out-of-sample claim. Changing execution,
            sizing or fees does not reset the count.
          </p>
          {confirming ? (
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                className="btn-danger"
                onClick={() => {
                  setConfirming(false);
                  onReveal();
                }}
                disabled={revealing}
              >
                {revealing ? "Revealing…" : "Reveal again — window is no longer clean"}
              </button>
              <button type="button" className="btn" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" className="btn" onClick={() => setConfirming(true)}>
              Reveal again
            </button>
          )}
        </div>
      ) : (
        <p className="text-[10px] leading-relaxed text-warn">
          This window has been viewed once. The reveal is logged; treat the result as no longer
          pristine.
        </p>
      )}

      {holdout.warning ? (
        <p className="text-[10px] leading-relaxed text-warn">{holdout.warning}</p>
      ) : null}
      {revealError ? (
        <p className="text-[10px] text-negative" role="alert">
          Reveal failed: {errorMessage(revealError)}
        </p>
      ) : null}
      <SourceNote>
        Reveals are append-only in the holdout ledger, keyed by universe, strategy hash and split.
      </SourceNote>
    </div>
  );
}
