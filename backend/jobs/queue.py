"""Process-isolated run queue.

Nautilus engines are not thread-safe, so each run is a separate Python
subprocess.  Concurrency defaults to one.  Cancellation is SIGTERM followed by
SIGKILL after a grace period; orphans are reaped on startup.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.config import LOGS_DIR, ROOT, SETTINGS
from backend.jobs import state


class QueueManager:
    def __init__(self, concurrency: int | None = None) -> None:
        self._slots = threading.Semaphore(concurrency or SETTINGS.concurrency)
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self) -> None:
        state.init_db()
        state.reap_orphans()

    def submit(self, run_id: str) -> None:
        t = threading.Thread(target=self._run, args=(run_id,), name=f"run-{run_id}", daemon=True)
        with self._lock:
            self._threads[run_id] = t
        t.start()

    def _run(self, run_id: str) -> None:
        with self._slots:
            run = state.get_run(run_id)
            if run is None or run["status"] not in ("queued",):
                return
            log_path = LOGS_DIR / f"{run_id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("wb") as log:
                env = os.environ.copy()
                env.setdefault("PYTHONUNBUFFERED", "1")
                cmd = [sys.executable, "-m", "backend.engine.worker", run_id]
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
                state.update_run(run_id, status="running", pid=proc.pid)
                state.append_event(run_id, "info", "worker started")
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
                    state.append_event(run_id, "info", "worker stopped after cancellation")

    def cancel(self, run_id: str, grace_s: float = 5.0) -> bool:
        with self._lock:
            proc = self._procs.get(run_id)
        if proc is None or proc.poll() is not None:
            return False
        state.update_run(run_id, status="cancelled")
        state.append_event(run_id, "warning", "cancellation requested (SIGTERM)")
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        deadline = time.time() + grace_s
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.1)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        state.append_event(run_id, "warning", "worker killed after grace period (SIGKILL)")
        return True

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(run_id)
        return proc is not None and proc.poll() is None

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


QUEUE = QueueManager()
