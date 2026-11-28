"""SQLite application state: runs, presets, saved rules, holdout ledger.

Generated state lives in ``var/app.db`` only.  The module is deliberately plain
sqlite3 so it works in the API process and in the worker subprocess.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from backend.config import APP_DB, ensure_dirs

#: A run in one of these states is finished; nothing else may move it.
TERMINAL_STATUSES = ("complete", "failed", "cancelled", "orphaned")

_LOCK = threading.RLock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  config_json TEXT NOT NULL,
  strategy_hash TEXT NOT NULL,
  run_hash TEXT NOT NULL,
  data_version TEXT NOT NULL,
  requested_profile TEXT,
  effective_profile TEXT,
  fallback_reason TEXT,
  fallback_rate REAL,
  error TEXT,
  pid INTEGER,
  manifest_json TEXT,
  holdout_reveals INTEGER NOT NULL DEFAULT 0,
  holdout_variants INTEGER NOT NULL DEFAULT 1,
  experiment_id TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdout_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  universe TEXT NOT NULL,
  strategy_hash TEXT NOT NULL,
  split_hash TEXT NOT NULL,
  run_id TEXT NOT NULL,
  revealed_at TEXT NOT NULL,
  variant_index INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  status TEXT NOT NULL,
  total INTEGER NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_holds_key ON holdout_ledger(universe, strategy_hash, split_hash);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(APP_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _LOCK, _conn() as conn:
        conn.executescript(_SCHEMA)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("config_json", "manifest_json"):
        if d.get(key):
            try:
                d[key.removesuffix("_json")] = json.loads(d[key])
            except Exception:  # noqa: BLE001
                d[key.removesuffix("_json")] = None
    return d


# --------------------------------------------------------------------------- runs


def create_run(
    run_id: str,
    config: dict[str, Any],
    strategy_hash: str,
    run_hash: str,
    data_version: str,
    requested_profile: str,
    effective_profile: str,
    fallback_reason: str | None = None,
    experiment_id: str | None = None,
) -> None:
    now = utcnow()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_id,status,created_at,updated_at,config_json,strategy_hash,run_hash,"
            "data_version,requested_profile,effective_profile,fallback_reason,experiment_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                "queued",
                now,
                now,
                json.dumps(config, sort_keys=True),
                strategy_hash,
                run_hash,
                data_version,
                requested_profile,
                effective_profile,
                fallback_reason,
                experiment_id,
            ),
        )


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "error",
        "pid",
        "manifest_json",
        "effective_profile",
        "fallback_reason",
        "fallback_rate",
        "holdout_reveals",
        "holdout_variants",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown run fields: {sorted(unknown)}")
    if "manifest_json" in fields and not isinstance(fields["manifest_json"], str):
        fields["manifest_json"] = json.dumps(fields["manifest_json"], sort_keys=True)
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    # Terminal statuses are final. A worker that finishes after a cancellation
    # (or a duplicate queue claim) must not resurrect the run.
    guard = ""
    status = fields.get("status")
    if status in ("queued", "running"):
        guard = " AND status NOT IN ('complete','failed','cancelled','orphaned')"
    elif status == "complete":
        guard = " AND status NOT IN ('failed','cancelled','orphaned')"
    with _LOCK, _conn() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE run_id=?{guard}", (*fields.values(), run_id))


def get_run(run_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())


RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "complete",
    "failed",
    "cancelled",
    "orphaned",
)


def _status_filter(
    status: str | None, data_version: str | None
) -> tuple[str, tuple[Any, ...]]:
    """SQL predicate for a run-status filter.

    A stored ``complete`` run whose data version is not the current one is
    displayed as ``orphaned``. The filter follows the same rule, so
    ``status=complete`` excludes it and ``status=orphaned`` includes it.
    """
    if not status:
        return "", ()
    current = data_version or ""
    if status == "complete":
        return (
            "WHERE status='complete' AND (data_version IS NULL OR data_version=?)",
            (current,),
        )
    if status == "orphaned":
        return (
            "WHERE (status='orphaned' OR "
            "(status='complete' AND data_version IS NOT NULL AND data_version<>?))",
            (current,),
        )
    return "WHERE status=?", (status,)


def list_runs(
    limit: int = 100,
    offset: int = 0,
    *,
    status: str | None = None,
    data_version: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _status_filter(status, data_version)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM runs {where} ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [r for row in rows if (r := _row(row)) is not None]


def count_runs(*, status: str | None = None, data_version: str | None = None) -> int:
    """True number of runs matching the filter, independent of paging."""
    where, params = _status_filter(status, data_version)
    with _conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM runs {where}", params).fetchone()
    return int(row[0]) if row else 0


def delete_run(run_id: str) -> bool:
    with _LOCK, _conn() as conn:
        cur = conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
    return cur.rowcount > 0


def claim_run(run_id: str) -> bool:
    """Atomically move a run from ``queued`` to ``running``.

    The queue calls this before spawning a worker so two submissions of the
    same run cannot both execute it.  Returns ``True`` only for the caller
    that won the transition.
    """
    with _LOCK, _conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET status='running', updated_at=? WHERE run_id=? AND status='queued'",
            (utcnow(), run_id),
        )
    return cur.rowcount == 1


def mark_cancelled(run_id: str) -> bool:
    """Move a queued/running run to ``cancelled``; ``True`` if it moved."""
    with _LOCK, _conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET status='cancelled', updated_at=? "
            "WHERE run_id=? AND status IN ('queued','running')",
            (utcnow(), run_id),
        )
    return cur.rowcount == 1


def append_event(run_id: str, level: str, message: str) -> None:
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO run_events (run_id,ts,level,message) VALUES (?,?,?,?)",
            (run_id, utcnow(), level, message),
        )


def events(run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
            (run_id, after, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------------ presets


def create_preset(preset_id: str, name: str, config: dict[str, Any]) -> None:
    now = utcnow()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO presets (id,name,config_json,created_at,updated_at) VALUES (?,?,?,?,?)",
            (preset_id, name, json.dumps(config, sort_keys=True), now, now),
        )


def list_presets() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY updated_at DESC").fetchall()
    out = []
    for row in rows:
        d = _row(row)
        if d:
            out.append(d)
    return out


def delete_preset(preset_id: str) -> bool:
    with _LOCK, _conn() as conn:
        cur = conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    return cur.rowcount > 0


# ----------------------------------------------------------------- saved strategies


def save_strategy(strategy_id: str, name: str, config: dict[str, Any]) -> None:
    now = utcnow()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO saved_strategies (id,name,config_json,created_at,updated_at) VALUES (?,?,?,?,?)",
            (strategy_id, name, json.dumps(config, sort_keys=True), now, now),
        )


def list_strategies() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM saved_strategies ORDER BY updated_at DESC").fetchall()
    out = []
    for row in rows:
        d = _row(row)
        if d:
            out.append(d)
    return out


def delete_strategy(strategy_id: str) -> bool:
    with _LOCK, _conn() as conn:
        cur = conn.execute("DELETE FROM saved_strategies WHERE id=?", (strategy_id,))
    return cur.rowcount > 0


# ----------------------------------------------------------------- holdout ledger


def record_reveal(universe: str, strategy_hash: str, split_hash: str, run_id: str) -> dict[str, Any]:
    now = utcnow()
    with _LOCK, _conn() as conn:
        count = conn.execute(
            "SELECT count(*) FROM holdout_ledger WHERE universe=? AND strategy_hash=? AND split_hash=?",
            (universe, strategy_hash, split_hash),
        ).fetchone()[0]
        seen_variants = conn.execute(
            "SELECT count(DISTINCT strategy_hash) FROM holdout_ledger WHERE universe=? AND split_hash=?",
            (universe, split_hash),
        ).fetchone()[0]
        existing_variant = conn.execute(
            "SELECT min(variant_index) FROM holdout_ledger WHERE universe=? AND split_hash=? AND strategy_hash=?",
            (universe, split_hash, strategy_hash),
        ).fetchone()[0]
        # A reveal keeps the variant index it was first assigned, even after
        # other strategies were revealed in between.
        variant = int(existing_variant) if existing_variant is not None else int(seen_variants) + 1
        conn.execute(
            "INSERT INTO holdout_ledger (universe,strategy_hash,split_hash,run_id,revealed_at,variant_index) "
            "VALUES (?,?,?,?,?,?)",
            (universe, strategy_hash, split_hash, run_id, now, variant),
        )
        conn.execute(
            "UPDATE runs SET holdout_reveals=?, holdout_variants=?, updated_at=? WHERE run_id=?",
            (count + 1, variant, now, run_id),
        )
    return {
        "reveal_count": count + 1,
        "variant_index": variant,
        "warning": count > 0,
        "message": (
            "This out-of-sample window has already been revealed for this strategy; "
            "it is no longer clean."
            if count > 0
            else "First reveal of this out-of-sample window; it has been recorded in the ledger."
        ),
        "revealed_at": now,
    }


def holdout_summary(universe: str, strategy_hash: str, split_hash: str) -> dict[str, Any]:
    with _conn() as conn:
        count = conn.execute(
            "SELECT count(*) FROM holdout_ledger WHERE universe=? AND strategy_hash=? AND split_hash=?",
            (universe, strategy_hash, split_hash),
        ).fetchone()[0]
    return {"reveal_count": count, "clean": count == 0}


# -------------------------------------------------------------------- experiments


def create_experiment(experiment_id: str, name: str, config: dict[str, Any], total: int) -> None:
    now = utcnow()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO experiments (id,name,config_json,status,total,completed,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (experiment_id, name, json.dumps(config, sort_keys=True), "queued", total, 0, now, now),
        )


def update_experiment(experiment_id: str, **fields: Any) -> None:
    allowed = {"status", "completed"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown experiment fields: {sorted(unknown)}")
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, _conn() as conn:
        conn.execute(f"UPDATE experiments SET {cols} WHERE id=?", (*fields.values(), experiment_id))


def bump_experiment(experiment_id: str) -> None:
    """Recompute completed/status from the child runs."""
    with _LOCK, _conn() as conn:
        rows = conn.execute(
            "SELECT status FROM runs WHERE experiment_id=?", (experiment_id,)
        ).fetchall()
        total = conn.execute(
            "SELECT total,status FROM experiments WHERE id=?", (experiment_id,)
        ).fetchone()
        if total is None:
            return
        statuses = [r["status"] for r in rows]
        done = len([s for s in statuses if s in TERMINAL_STATUSES])
        status = "running"
        if rows and done == len(rows) and done == total[0]:
            if all(s == "complete" for s in statuses):
                status = "complete"
            elif all(s == "cancelled" for s in statuses) or total[1] == "cancelled":
                status = "cancelled"
            else:
                status = "failed"
        conn.execute(
            "UPDATE experiments SET completed=?, status=?, updated_at=? WHERE id=?",
            (done, status, utcnow(), experiment_id),
        )


def get_experiment(experiment_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        d = _row(conn.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone())
        if not d:
            return None
        d["runs"] = [
            _row(r) for r in conn.execute("SELECT * FROM runs WHERE experiment_id=? ORDER BY created_at", (experiment_id,)).fetchall()
        ]
    return d


def list_experiments() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
    return [d for row in rows if (d := _row(row)) is not None]


# ------------------------------------------------------------------- orphan reap


def _alive(pid: int) -> bool:
    """True when ``pid`` is a live, non-zombie process."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return False
    except OSError:
        raw = b""
    if raw:
        try:
            state = raw.rsplit(b")", 1)[1].split()[0]
            return state != b"Z"
        except (IndexError, ValueError):
            pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_worker(pid: int, grace_s: float = 5.0) -> bool:
    """SIGTERM the worker's process group, SIGKILL after ``grace_s``.

    Returns ``True`` when the process is gone afterwards.  Workers are started
    with ``start_new_session=True`` so the pid is also the process-group id.
    """
    if not _alive(pid):
        return True

    def _signal(sig: int) -> bool:
        try:
            os.killpg(pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
                return True
            except (ProcessLookupError, PermissionError):
                return False

    _signal(signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    _signal(signal.SIGKILL)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def reap_orphans(grace_s: float = 5.0) -> list[str]:
    """Mark queued/running runs whose worker is gone as failed.

    Called on API startup.  A live worker left over from a previous API
    process is terminated (SIGTERM, then SIGKILL after the grace period) so it
    cannot keep writing into a run this process now owns.  A run whose data
    version disappeared is not touched here; the API labels it orphaned by
    comparing versions and never re-runs it.
    """
    with _conn() as conn:
        rows = conn.execute("SELECT run_id,pid,status FROM runs WHERE status IN ('queued','running')").fetchall()
    reaped: list[str] = []
    for row in rows:
        run_id = row["run_id"]
        pid = row["pid"]
        if pid and int(pid) != os.getpid():
            if _alive(int(pid)):
                terminate_worker(int(pid), grace_s)
                message = (
                    f"worker process {pid} survived an API restart; "
                    "terminated as an orphan"
                )
            else:
                message = "worker process was lost; run reaped on startup"
        elif pid and int(pid) == os.getpid():
            message = "run was owned by the API process; reaped on startup"
        else:
            message = "run had no worker process; reaped on startup"
        with _LOCK, _conn() as conn:
            conn.execute(
                "UPDATE runs SET status='failed', error=?, updated_at=? WHERE run_id=?",
                (message, utcnow(), run_id),
            )
            conn.execute(
                "INSERT INTO run_events (run_id,ts,level,message) VALUES (?,?,?,?)",
                (run_id, utcnow(), "error", message),
            )
        reaped.append(run_id)
    return reaped
