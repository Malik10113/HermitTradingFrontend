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
