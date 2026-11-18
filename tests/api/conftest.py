"""Fixtures for API/jobs hardening tests.

Everything runs offline against a temporary SQLite state file and temporary
``var/`` directories.  Workers are real subprocesses driven by a small script,
so cancellation and orphan reaping exercise actual process signals rather than
mocks.  No sibling data is read and no network is used.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.main import app

# Writes ``start <pid>`` once and a ``tick <pid>`` every 50 ms until killed.
SLOW_WORKER_SRC = '''
import os, sys, time

MARKER = __MARKER__


def note(line):
    with open(MARKER, "a") as fh:
        fh.write(line + "\\n")


note("start %d" % os.getpid())
while True:
    note("tick %d" % os.getpid())
    time.sleep(0.05)
'''

# Serialises across processes with a lock directory; a second concurrent
# worker would log ``overlap``.
SERIAL_WORKER_SRC = '''
import os, sys, time

MARKER = __MARKER__
LOCK = __LOCK__


def note(line):
    with open(MARKER, "a") as fh:
        fh.write(line + "\\n")


note("start %d" % os.getpid())
try:
    os.mkdir(LOCK)
    owned = True
except FileExistsError:
    note("overlap %d" % os.getpid())
    owned = False
time.sleep(0.25)
if owned:
    os.rmdir(LOCK)
note("end %d" % os.getpid())
'''


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def pid_alive(pid: int) -> bool:
    """False only when the pid is gone (a zombie counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return False
    except OSError:
        raw = b""
    if raw:
        try:
            return raw.rsplit(b")", 1)[1].split()[0] != b"Z"
        except (IndexError, ValueError):
            pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class ApiEnv:
    tmp_path: Path
    runs: Path
    logs: Path
    marker: Path
    client: TestClient
    queue: object
    submitted: list[str] = field(default_factory=list)

    def marker_lines(self) -> list[str]:
        if not self.marker.exists():
            return []
        return [line for line in self.marker.read_text().splitlines() if line]


def _write_worker(tmp_path: Path, name: str, source: str, **values: str) -> Path:
    script = tmp_path / name
    for key, value in values.items():
        source = source.replace(f"__{key}__", repr(value))
    script.write_text(source)
    return script


def _fake_prepare(monkeypatch, submitted):
    from backend.api import routes

    def prepare(req):
        submitted.append(getattr(req.strategy, "instrument_id", "x"))
        return {
            "config": {
                "strategy": {"instrument_id": req.strategy.instrument_id},
                "execution": {},
                "risk": {},
                "evaluation": {},
            },
            "strategy_hash": "sha256:strategy",
            "run_hash": "sha256:run",
            "data_version": "sha256:test",
            "requested_profile": "frictionless",
            "effective_profile": "frictionless",
            "fallback_reason": None,
        }

    monkeypatch.setattr(routes, "_prepare", prepare)


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    from backend.api import routes
    from backend.jobs import queue, state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(queue, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(routes, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(routes, "run_data_version", lambda: "sha256:test")
    (tmp_path / "runs").mkdir()

    marker = tmp_path / "worker.log"
    script = _write_worker(tmp_path, "slow_worker.py", SLOW_WORKER_SRC, MARKER=str(marker))
    manager = queue.QueueManager(
        concurrency=1,
        worker_cmd=[sys.executable, str(script)],
        cancel_grace_s=2.0,
    )
    monkeypatch.setattr(routes, "QUEUE", manager)

    env = ApiEnv(
        tmp_path=tmp_path,
        runs=tmp_path / "runs",
        logs=tmp_path / "logs",
        marker=marker,
        client=None,  # type: ignore[arg-type]
        queue=manager,
    )
    _fake_prepare(monkeypatch, env.submitted)

    with TestClient(app) as client:
        env.client = client
        try:
            yield env
        finally:
            manager.shutdown(grace_s=0.5)


@pytest.fixture()
def serial_queue(tmp_path, monkeypatch):
    """A queue whose worker records concurrent overlap."""
    from backend.jobs import queue, state

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(queue, "LOGS_DIR", tmp_path / "logs")
    marker = tmp_path / "serial.log"
    lock = tmp_path / "serial.lock"
    script = _write_worker(
        tmp_path, "serial_worker.py", SERIAL_WORKER_SRC, MARKER=str(marker), LOCK=str(lock)
    )
    state.init_db()
    manager = queue.QueueManager(
        concurrency=1,
        worker_cmd=[sys.executable, str(script)],
        cancel_grace_s=2.0,
    )
    try:
        yield SimpleNamespace(queue=manager, marker=marker, state=state, tmp_path=tmp_path)
    finally:
        manager.shutdown(grace_s=0.5)


def submit_run(client: TestClient, instrument_id: str = "x-YES.POLYMARKET") -> str:
    response = client.post(
        "/api/backtests",
        json={
            "strategy": {"instrument_id": instrument_id, "builtin_id": "buy_and_hold"},
            "evaluation": {"date_range": {"from": "2026-01-01", "to": "2026-01-02"}},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def create_run_row(run_id: str, *, data_version: str = "sha256:test", experiment_id=None) -> None:
    from backend.jobs import state

    state.create_run(
        run_id,
        {"strategy": {"instrument_id": "x-YES.POLYMARKET"}},
        "sha256:strategy",
        "sha256:run",
        data_version,
        "frictionless",
        "frictionless",
        experiment_id=experiment_id,
    )
