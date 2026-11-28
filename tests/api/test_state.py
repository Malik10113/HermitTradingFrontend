"""Application state tests (SQLite only, no network)."""

from __future__ import annotations

from backend.jobs import state


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.db")
    state.init_db()


def test_run_lifecycle(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run(
        "run_1",
        {"strategy": {"instrument_id": "x-YES.POLYMARKET"}},
        "sha256:s",
        "sha256:r",
        "sha256:d",
        "book",
        "conservative",
        "no_book_coverage",
    )
    run = state.get_run("run_1")
    assert run is not None
    assert run["status"] == "queued"
    assert run["config"]["strategy"]["instrument_id"] == "x-YES.POLYMARKET"
    state.update_run("run_1", status="running", pid=1234)
    assert state.get_run("run_1")["status"] == "running"
    state.append_event("run_1", "info", "hello")
    evs = state.events("run_1")
    assert evs[-1]["message"] == "hello"
    assert state.delete_run("run_1")
    assert state.get_run("run_1") is None


def test_reveal_ledger_warns_on_second_reveal(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run("run_1", {}, "sha256:s", "sha256:r", "sha256:d", "conservative", "conservative")
    first = state.record_reveal("x-YES.POLYMARKET", "sha256:s", "sha256:split", "run_1")
    assert first["reveal_count"] == 1
    assert first["warning"] is False
    second = state.record_reveal("x-YES.POLYMARKET", "sha256:s", "sha256:split", "run_1")
    assert second["reveal_count"] == 2
    assert second["warning"] is True
    assert state.holdout_summary("x-YES.POLYMARKET", "sha256:s", "sha256:split") == {
        "reveal_count": 2,
        "clean": False,
    }


def test_update_run_never_leaves_a_terminal_status(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run("run_cancelled", {}, "sha256:s", "sha256:r", "sha256:d", "conservative", "conservative")
    assert state.mark_cancelled("run_cancelled")
    state.update_run("run_cancelled", status="running", pid=4242)
    assert state.get_run("run_cancelled")["status"] == "cancelled"
    state.update_run("run_cancelled", status="complete")
    assert state.get_run("run_cancelled")["status"] == "cancelled"
    # A crash report from a worker that exited late must not overwrite it.
    state.update_run("run_cancelled", status="failed", error="worker exited")
    assert state.get_run("run_cancelled")["status"] == "cancelled"

    state.create_run("run_complete", {}, "sha256:s", "sha256:r", "sha256:d", "conservative", "conservative")
    state.update_run("run_complete", status="complete")
    state.update_run("run_complete", status="running")
    assert state.get_run("run_complete")["status"] == "complete"
    state.update_run("run_complete", status="failed")
    assert state.get_run("run_complete")["status"] == "complete"
    # Fields other than status still update on a terminal run.
    state.update_run("run_complete", error="late")
    run = state.get_run("run_complete")
    assert run["status"] == "complete"
    assert run["error"] == "late"


def test_reveal_variant_index_survives_interleaved_strategies(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run("run_a", {}, "sha256:a", "sha256:r1", "sha256:d", "conservative", "conservative")
    state.create_run("run_b", {}, "sha256:b", "sha256:r2", "sha256:d", "conservative", "conservative")
    first_a = state.record_reveal("x-YES.POLYMARKET", "sha256:a", "sha256:split", "run_a")
    first_b = state.record_reveal("x-YES.POLYMARKET", "sha256:b", "sha256:split", "run_b")
    again_a = state.record_reveal("x-YES.POLYMARKET", "sha256:a", "sha256:split", "run_a")
    assert first_a["variant_index"] == 1
    assert first_b["variant_index"] == 2
    # A re-reveal keeps the index it was first assigned, not the current count.
    assert again_a["variant_index"] == 1
    assert again_a["reveal_count"] == 2
    assert again_a["warning"] is True


def test_presets_and_saved_strategies(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_preset("p1", "Demo", {"a": 1})
    assert state.list_presets()[0]["config"] == {"a": 1}
    state.save_strategy("s1", "EMA", {"strategy": {}})
    assert state.list_strategies()[0]["name"] == "EMA"
    assert state.delete_preset("p1")
    assert state.delete_strategy("s1")


def test_reap_orphans_marks_lost_workers(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run("run_1", {}, "sha256:s", "sha256:r", "sha256:d", "conservative", "conservative")
    state.update_run("run_1", status="running", pid=999_999_999)
    reaped = state.reap_orphans()
    assert "run_1" in reaped
    assert state.get_run("run_1")["status"] == "failed"


def test_holdout_variant_counter_tracks_strategy_hashes(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    state.create_run("run_1", {}, "sha256:a", "sha256:r1", "sha256:d", "conservative", "conservative")
    state.create_run("run_2", {}, "sha256:b", "sha256:r2", "sha256:d", "conservative", "conservative")
    first = state.record_reveal("x-YES.POLYMARKET", "sha256:a", "sha256:split", "run_1")
    again = state.record_reveal("x-YES.POLYMARKET", "sha256:a", "sha256:split", "run_1")
    variant = state.record_reveal("x-YES.POLYMARKET", "sha256:b", "sha256:split", "run_2")
    assert first["variant_index"] == 1
    assert again["variant_index"] == 1
    assert again["warning"] is True
    assert variant["variant_index"] == 2


def test_reap_orphans_terminates_a_live_worker(tmp_path, monkeypatch):
    import subprocess

    _init(tmp_path, monkeypatch)
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        state.create_run("run_live", {}, "sha256:s", "sha256:r", "sha256:d", "conservative", "conservative")
        state.update_run("run_live", status="running", pid=proc.pid)
        reaped = state.reap_orphans()
        assert "run_live" in reaped
        assert proc.wait(timeout=5) != 0
        assert state.get_run("run_live")["status"] == "failed"
    finally:
        if proc.poll() is None:
            proc.kill()
