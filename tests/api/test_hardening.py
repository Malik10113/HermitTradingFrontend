"""Hardening tests: cancellation, orphan reaping, SSE, auth, cache, queue.

The contract is docs/BRIEF.md §8.5/§9 and AGENTS.md §8.  Every test is offline,
uses a temporary state file and temporary ``var/`` directories, and drives real
subprocesses for the queue tests.  No sibling data is read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.jobs import state
from tests.api.conftest import (
    ApiEnv,
    create_run_row,
    pid_alive,
    submit_run,
    wait_until,
)


def _json_frames(frames: list[dict[str, str]]) -> list[dict]:
    out = []
    for frame in frames:
        if frame["event"] in ("message", "status", "end"):
            out.append(json.loads(frame["data"]))
    return out


def read_sse(client: TestClient, url: str, timeout: float = 6.0) -> list[dict[str, str]]:
    """Collect SSE frames until the named ``end`` event or the deadline."""
    frames: list[dict[str, str]] = []
    event = None
    data: list[str] = []
    deadline = time.monotonic() + timeout
    with client.stream("GET", url) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                break
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                pass
            elif line.startswith("data:"):
                data.append(line.split(":", 1)[1].strip())
            elif line == "":
                if event or data:
                    frames.append({"event": event or "message", "data": "\n".join(data)})
                if event == "end":
                    break
                event, data = None, []
    return frames


# ------------------------------------------------------------------ 1. cancel


def test_cancel_running_run_terminates_worker(api_env: ApiEnv) -> None:
    run_id = submit_run(api_env.client)
    assert wait_until(
        lambda: (run := state.get_run(run_id)) is not None
        and run["status"] == "running"
        and bool(run["pid"]),
        timeout=8.0,
    ), "run never reached running with a pid"
    assert wait_until(lambda: "start " in api_env.marker.read_text() if api_env.marker.exists() else False)
    run = state.get_run(run_id)
    pid = int(run["pid"])
    assert pid_alive(pid)

    started = time.monotonic()
    assert api_env.queue.cancel(run_id) is True
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"cancellation took {elapsed:.2f}s"
    assert state.get_run(run_id)["status"] == "cancelled"
    assert wait_until(lambda: not api_env.queue.is_running(run_id), timeout=2.0)
    assert not pid_alive(pid), "worker process still alive after cancellation"

    # No further writes: the worker is dead and the queue must not overwrite
    # the cancelled status when the process exit is observed.
    before = api_env.marker.read_text()
    events_before = len(state.events(run_id))
    time.sleep(0.4)
    assert api_env.marker.read_text() == before
    assert len(state.events(run_id)) == events_before
    assert state.get_run(run_id)["status"] == "cancelled"
    # The worker log is kept for inspection after cancellation.
    assert (api_env.logs / f"{run_id}.log").exists()


def test_delete_running_run_cancels_worker_before_removing_state(api_env: ApiEnv) -> None:
    run_id = submit_run(api_env.client)
    assert wait_until(
        lambda: (run := state.get_run(run_id)) is not None
        and run["status"] == "running"
        and bool(run["pid"]),
        timeout=8.0,
    )
    assert wait_until(lambda: api_env.marker.exists() and "start " in api_env.marker.read_text())
    run_dir = api_env.runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sentinel = run_dir / "summary.json"
    sentinel.write_text('{"note": "artifact for inspection"}')
    pid = int(state.get_run(run_id)["pid"])

    response = api_env.client.delete(f"/api/backtests/{run_id}")

    assert response.status_code == 204
    assert state.get_run(run_id) is None
    assert not run_dir.exists(), "artifacts were not removed"
    assert wait_until(lambda: not api_env.queue.is_running(run_id), timeout=2.0)
    assert not pid_alive(pid)

    before = api_env.marker.read_text()
    time.sleep(0.3)
    assert api_env.marker.read_text() == before


# ------------------------------------------------------------ 2. orphan reaping


def test_startup_reaps_live_and_dead_workers(tmp_path, monkeypatch) -> None:
    from backend.jobs import queue

    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(queue, "LOGS_DIR", tmp_path / "logs")
    state.init_db()

    # A dead pid.
    create_run_row("run_dead")
    state.update_run("run_dead", status="running", pid=999_999_999)

    # A queued row with no worker at all.
    create_run_row("run_queued")
    state.update_run("run_queued", status="queued")

    # A live worker left behind by a previous API process.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        create_run_row("run_live")
        state.update_run("run_live", status="running", pid=proc.pid)

        manager = queue.QueueManager(worker_cmd=[sys.executable, "-c", "pass"], cancel_grace_s=2.0)
        manager.start()

        assert wait_until(lambda: proc.poll() is not None, timeout=3.0), "live orphan was not terminated"
        dead = state.get_run("run_dead")
        live = state.get_run("run_live")
        queued = state.get_run("run_queued")
        assert dead["status"] == "failed" and "lost" in dead["error"]
        assert live["status"] == "failed" and "orphan" in live["error"]
        assert queued["status"] == "failed" and queued["error"]
        assert any("reaped on startup" in e["message"] for e in state.events("run_dead"))
        assert any("terminated as an orphan" in e["message"] for e in state.events("run_live"))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_completed_run_with_changed_data_version_is_orphaned_not_rerun(
    api_env: ApiEnv, monkeypatch
) -> None:
    from backend.api import routes

    create_run_row("run_done", data_version="sha256:old")
    state.update_run("run_done", status="complete")

    submitted: list[str] = []
    monkeypatch.setattr(api_env.queue, "submit", lambda run_id: submitted.append(run_id))
    # The current version is now absent (for example the source dataset moved).
    monkeypatch.setattr(routes, "run_data_version", lambda: None)

    body = api_env.client.get("/api/backtests/run_done").json()
    assert body["status"] == "orphaned"
    listing = api_env.client.get("/api/backtests").json()
    assert listing["items"][0]["status"] == "orphaned"

    assert submitted == [], "an orphaned completed run was re-submitted"
    stored = state.get_run("run_done")
    assert stored["status"] == "complete", "the stored status must not be rewritten"
    assert stored["data_version"] == "sha256:old"


# --------------------------------------------------------------------- 3. SSE


def _seed_run(run_id: str, status: str) -> None:
    create_run_row(run_id)
    state.update_run(run_id, status=status)


def test_sse_emits_default_and_named_frames_and_ends_on_fast_terminal(api_env: ApiEnv) -> None:
    _seed_run("run_fast", "complete")
    state.append_event("run_fast", "info", "complete")

    frames = read_sse(api_env.client, "/api/backtests/run_fast/events")

    named = [f["event"] for f in frames]
    assert "status" in named
    assert "end" in named
    assert "log" in named, f"log events were not streamed: {named}"
    # Default-message frames so EventSource.onmessage sees status changes.
    default = [json.loads(f["data"]) for f in frames if f["event"] == "message"]
    assert {"status": "complete"} in default
    end = next(f for f in frames if f["event"] == "end")
    assert json.loads(end["data"])["status"] == "complete"
    # Ended, so no keep-alive or trailing status spam follows.
    assert named[-1] == "end"


def test_sse_streams_transitions_and_never_misses_a_fast_terminal(api_env: ApiEnv) -> None:
    _seed_run("run_move", "queued")

    def advance() -> None:
        time.sleep(0.15)
        state.update_run("run_move", status="running")
        time.sleep(0.75)
        state.update_run("run_move", status="complete")

    thread = threading.Thread(target=advance, daemon=True)
    thread.start()
    frames = read_sse(api_env.client, "/api/backtests/run_move/events")
    thread.join(timeout=2.0)

    statuses = [json.loads(f["data"])["status"] for f in frames if f["event"] == "status"]
    assert statuses[0] == "queued"
    assert statuses[-1] == "complete"
    assert "running" in statuses
    assert [f["event"] for f in frames][-1] == "end"
    # One named status frame per transition plus its default-message twin.
    assert len(statuses) == len(set(statuses)), statuses


def test_sse_polls_instead_of_busy_looping(api_env: ApiEnv, monkeypatch) -> None:
    from backend.api import routes

    _seed_run("run_idle", "queued")
    calls = {"n": 0}
    real_events = state.events

    def counted_events(run_id: str, after: int = 0, limit: int = 500):
        calls["n"] += 1
        return real_events(run_id, after=after, limit=limit)

    monkeypatch.setattr(state, "events", counted_events)

    def finish() -> None:
        time.sleep(1.2)
        state.update_run("run_idle", status="failed", error="test")

    thread = threading.Thread(target=finish, daemon=True)
    thread.start()
    started = time.monotonic()
    frames = read_sse(api_env.client, "/api/backtests/run_idle/events")
    elapsed = time.monotonic() - started
    thread.join(timeout=2.0)

    assert elapsed >= 1.0
    assert calls["n"] <= 8, f"event stream polled {calls['n']} times; it is busy-looping"
    statuses = [json.loads(f["data"])["status"] for f in frames if f["event"] == "status"]
    assert statuses == ["queued", "failed"], statuses


# --------------------------------------------------------------- 4. APP_TOKEN


def test_app_token_protects_every_api_route_but_serves_static(api_env: ApiEnv, tmp_path, monkeypatch) -> None:
    from backend import main as main_module
    from backend.config import SETTINGS

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>htf</title>")
    (dist / "assets" / "app.js").write_text("console.log('htf')")
    monkeypatch.setattr(main_module, "DIST", dist)

    object.__setattr__(SETTINGS, "app_token", "s3cret-token")
    try:
        app = main_module.create_app()
        with TestClient(app) as client:
            missing = client.get("/api/health")
            assert missing.status_code == 401
            body = missing.json()
            assert body["code"] == "unauthorized"
            assert body["message"]
            assert "details" in body

            assert client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
            # Scheme is case-insensitive; the token is compared exactly.
            assert (
                client.get("/api/health", headers={"Authorization": "bearer s3cret-token"}).status_code
                == 200
            )
            assert client.get("/api/meta", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
            assert client.get("/api/indicators").status_code == 401

            # Static assets are outside the API router and stay public.
            index = client.get("/")
            assert index.status_code == 200 and "htf" in index.text
            assert client.get("/assets/app.js").status_code == 200
    finally:
        object.__setattr__(SETTINGS, "app_token", None)


# --------------------------------------------------------------- 5. cache stats


def test_cache_stats_reflects_indicator_preview(api_env: ApiEnv, monkeypatch) -> None:
    from backend.api import services
    from backend.data import readmodel
    from backend.indicators.cache import clear_cache
    from tests.indicators.fixtures.bars import make_bars

    bars = make_bars(30)
    for index, bar in enumerate(bars):
        bar["ts_event_ns"] = 1_700_000_000_000_000_000 + index * 60_000_000_000

    clear_cache()
    monkeypatch.setattr(services, "ensure_bars", lambda *args, **kwargs: None)
    monkeypatch.setattr(services, "run_data_version", lambda: "sha256:test")
    monkeypatch.setattr(
        readmodel,
        "load_bars",
        lambda *args, **kwargs: {"bars": bars, "bar_type": "1m", "data_version": "sha256:test"},
    )

    body = {
        "instrument_id": "x-YES.POLYMARKET",
        "bar_type": "1m",
        "indicators": [{"instance_id": "sma3", "type": "sma", "params": {"period": 3}}],
    }

    first = api_env.client.post("/api/indicators/preview", json=body)
    assert first.status_code == 200, first.text
    after_miss = api_env.client.get("/api/cache/stats").json()
    assert set(after_miss) >= {"hits", "misses", "size", "capacity"}
    assert after_miss["misses"] == 1
    assert after_miss["hits"] == 0
    assert after_miss["size"] == 1
    assert after_miss["capacity"] >= 1

    second = api_env.client.post("/api/indicators/preview", json=body)
    assert second.status_code == 200
    after_hit = api_env.client.get("/api/cache/stats").json()
    assert after_hit["hits"] == 1, "preview did not exercise the indicator cache"
    assert after_hit["misses"] == 1
    assert after_hit["size"] == 1
    # Cached and uncached previews return the same series (no fabrication).
    assert second.json()["series"] == first.json()["series"]


# ------------------------------------------------------------ 6. queue robustness


def test_queue_concurrency_is_one(serial_queue) -> None:
    manager = serial_queue.queue
    for index in range(3):
        run_id = f"run_serial_{index}"
        create_run_row(run_id)
        manager.submit(run_id)

    assert wait_until(
        lambda: all(
            (run := state.get_run(f"run_serial_{i}")) is not None and run["status"] == "complete"
            for i in range(3)
        ),
        timeout=15.0,
    )
    lines = serial_queue.marker.read_text().splitlines()
    starts = [line for line in lines if line.startswith("start ")]
    ends = [line for line in lines if line.startswith("end ")]
    assert len(starts) == 3, lines
    assert len(ends) == 3, lines
    assert not any(line.startswith("overlap") for line in lines), f"workers overlapped: {lines}"
    # start/end pairs never interleave.
    assert [line.split()[0] for line in lines] == ["start", "end"] * 3


def test_duplicate_submission_executes_once(api_env: ApiEnv) -> None:
    create_run_row("run_twice")
    for _ in range(2):
        api_env.queue.submit("run_twice")

    assert wait_until(
        lambda: api_env.marker.exists() and "start " in api_env.marker.read_text(), timeout=8.0
    )
    time.sleep(0.3)
    starts = [line for line in api_env.marker.read_text().splitlines() if line.startswith("start ")]
    assert len(starts) == 1, f"run executed {len(starts)} times: {starts}"


def test_experiment_cancel_stops_every_non_terminal_child(api_env: ApiEnv) -> None:
    state.create_experiment("exp_cancel", "cancel me", {}, 3)
    for index in range(3):
        run_id = f"run_exp_{index}"
        create_run_row(run_id, experiment_id="exp_cancel")
        api_env.queue.submit(run_id)
    assert wait_until(
        lambda: (run := state.get_run("run_exp_0")) is not None
        and run["status"] == "running"
        and bool(run["pid"]),
        timeout=8.0,
    )
    pid = int(state.get_run("run_exp_0")["pid"])

    response = api_env.client.post("/api/experiments/exp_cancel/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["cancelled"]) == {"run_exp_0", "run_exp_1", "run_exp_2"}
    assert body["completed"] == 3
    assert body["total"] == 3
    assert body["status"] == "cancelled"

    for index in range(3):
        assert state.get_run(f"run_exp_{index}")["status"] == "cancelled"
    assert wait_until(lambda: not api_env.queue.is_running("run_exp_0"), timeout=2.0)
    assert not pid_alive(pid)

    # Only the first child ever started; the queued ones must not run after
    # cancellation.
    time.sleep(0.4)
    starts = [line for line in api_env.marker.read_text().splitlines() if line.startswith("start ")]
    assert len(starts) == 1, f"cancelled experiment started {len(starts)} workers"

    experiment = api_env.client.get("/api/experiments/exp_cancel").json()
    assert experiment["completed"] == 3
    assert experiment["total"] == 3
    assert experiment["status"] == "cancelled"


# ---------------------------------------------------------- 7. run_server.sh


def test_run_server_refuses_public_bind_and_missing_data(tmp_path) -> None:
    script = "scripts/run_server.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith("HTF_")}

    public = subprocess.run(
        ["bash", script],
        env={**env, "HTF_HOST": "0.0.0.0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert public.returncode == 2
    assert "refusing to bind" in public.stderr
    assert "tailscale serve" in public.stderr

    missing = subprocess.run(
        ["bash", script],
        env={**env, "HTF_DATA_ROOT": str(tmp_path / "no-such-data")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert missing.returncode == 3
    assert "data root not found" in missing.stderr
