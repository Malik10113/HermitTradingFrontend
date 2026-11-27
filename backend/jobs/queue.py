"""Process-isolated run queue.

Nautilus engines are not thread-safe, so each run is a separate Python
subprocess.  Concurrency defaults to one.  Cancellation is SIGTERM followed by
SIGKILL after a grace period; orphans are reaped on startup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from backend.config import LOGS_DIR, ROOT, SETTINGS
from backend.jobs import state

DEFAULT_WORKER_CMD = (sys.executable, "-m", "backend.engine.worker")


class QueueManager:
    def __init__(
        self,
        concurrency: int | None = None,
        worker_cmd: list[str] | tuple[str, ...] | None = None,
        cancel_grace_s: float = 5.0,
    ) -> None:
        self._slots = threading.Semaphore(concurrency or SETTINGS.concurrency)
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._worker_cmd = list(worker_cmd) if worker_cmd else list(DEFAULT_WORKER_CMD)
        self._cancel_grace_s = cancel_grace_s

    def start(self) -> None:
        state.init_db()
        state.reap_orphans(grace_s=self._cancel_grace_s)

    def submit(self, run_id: str) -> None:
        """Schedule ``run_id`` once.

        A second submission while the first is pending is a no-op; the run is
        claimed atomically in the database before a worker is spawned, so a
        duplicate that slips past this guard still cannot execute twice.
        """
        with self._lock:
            existing = self._threads.get(run_id)
            if existing is not None and existing.is_alive():
                return
            t = threading.Thread(target=self._run, args=(run_id,), name=f"run-{run_id}", daemon=True)
            self._threads[run_id] = t
        t.start()

    def _run(self, run_id: str) -> None:
        try:
            with self._slots:
                if not state.claim_run(run_id):
                    return
                run = state.get_run(run_id)
                if run is None:
                    return
                log_path = LOGS_DIR / f"{run_id}.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("wb") as log:
                    env = os.environ.copy()
                    env.setdefault("PYTHONUNBUFFERED", "1")
                    cmd = [*self._worker_cmd, run_id]
                    try:
                        proc = subprocess.Popen(
                            cmd,
                            cwd=str(ROOT),
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            env=env,
                            start_new_session=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        state.update_run(run_id, status="failed", error=f"failed to start worker: {exc}")
                        state.append_event(run_id, "error", f"failed to start worker: {exc}")
                        return
                    with self._lock:
                        self._procs[run_id] = proc
                    state.update_run(run_id, pid=proc.pid)
                    state.append_event(run_id, "info", "worker started")
                    # A cancellation may have landed while the worker was
                    # being spawned; do not let it run on.
                    current = state.get_run(run_id)
                    if current is None or current["status"] == "cancelled":
                        state.terminate_worker(proc.pid, self._cancel_grace_s)
                        proc.wait()
                        if current is not None:
                            state.append_event(run_id, "info", "worker stopped after cancellation")
                        return
                    code = proc.wait()
                with self._lock:
                    self._procs.pop(run_id, None)
                current = state.get_run(run_id)
                if current and current["status"] in ("queued", "running"):
                    if code == 0:
                        state.update_run(run_id, status="complete")
                    else:
                        tail = ""
                        try:
                            tail = log_path.read_text(errors="replace")[-2000:]
                        except Exception:  # noqa: BLE001
                            pass
                        state.update_run(
                            run_id,
                            status="failed",
                            error=f"worker exited with code {code}\n{tail}",
                        )
                        state.append_event(run_id, "error", f"worker exited with code {code}")
                elif current and current["status"] == "cancelled":
                    pass
        finally:
            with self._lock:
                self._procs.pop(run_id, None)
                thread = self._threads.get(run_id)
                if thread is threading.current_thread():
                    self._threads.pop(run_id, None)

    def request_cancel(self, run_id: str) -> bool:
        """Mark a queued/running run cancelled without signalling yet.

        Called before any worker is stopped so queued siblings of a cancelled
        experiment cannot claim the freed queue slot while the cancel loop is
        still running.
        """
        moved = state.mark_cancelled(run_id)
        if moved:
            state.append_event(run_id, "warning", "cancellation requested (SIGTERM)")
        return moved

    def stop(self, run_id: str, grace_s: float | None = None) -> None:
        """SIGTERM (then SIGKILL) a run's worker if one is running."""
        grace = self._cancel_grace_s if grace_s is None else grace_s
        with self._lock:
            proc = self._procs.get(run_id)
            thread = self._threads.get(run_id)
        if proc is None or proc.poll() is not None:
            return
        state.terminate_worker(proc.pid, grace)
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        state.append_event(run_id, "warning", "worker terminated after cancellation")

    def cancel(self, run_id: str, grace_s: float | None = None) -> bool:
        """Cancel a queued or running run and stop its worker.

        The status change is recorded first, so a worker that is still
        starting (or that finishes between the signal and the next poll) can
        never move the run back to ``complete``.  Returns ``True`` when the run
        was still non-terminal.
        """
        moved = self.request_cancel(run_id)
        self.stop(run_id, grace_s)
        return moved

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(run_id)
        return proc is not None and proc.poll() is None

    def shutdown(self, grace_s: float | None = None) -> None:
        """Stop every worker this manager started and wait for its thread."""
        grace = self._cancel_grace_s if grace_s is None else grace_s
        with self._lock:
            procs = list(self._procs.values())
            threads = list(self._threads.values())
        for proc in procs:
            if proc.poll() is None:
                state.terminate_worker(proc.pid, grace)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        current = threading.current_thread()
        deadline = time.monotonic() + grace + 2.0
        for thread in threads:
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(timeout=remaining)
        with self._lock:
            self._procs.clear()
            self._threads.clear()


QUEUE = QueueManager()
