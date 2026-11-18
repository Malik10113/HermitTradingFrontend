"""Worker entry point: python -m backend.engine.worker <run_id>."""

from __future__ import annotations

import sys
import traceback

from backend.engine.artifacts import write_artifacts
from backend.engine.runner import execute_run
from backend.jobs import state


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m backend.engine.worker <run_id>", file=sys.stderr)
        return 2
    run_id = argv[1]
    state.init_db()
    run = state.get_run(run_id)
    if run is None:
        print(f"run {run_id} not found", file=sys.stderr)
        return 2
    if run["status"] not in ("queued", "running"):
        print(f"run {run_id} is {run['status']}; refusing to execute", file=sys.stderr)
        return 0
    state.update_run(run_id, status="running")
    state.append_event(run_id, "info", "validating and loading data")
    try:
        config = run.get("config") or {}
        outcome = execute_run(config)
        state.append_event(run_id, "info", "writing artifacts")
        manifest = outcome.manifest
        manifest["run_id"] = run_id
        manifest = write_artifacts(run_id, config, manifest, outcome.result or {}, outcome.log)
        current = state.get_run(run_id)
        if current and current["status"] == "cancelled":
            state.append_event(run_id, "warning", "run cancelled; artifacts written for inspection")
            return 0
        state.update_run(
            run_id,
            status="complete",
            manifest_json=manifest,
            fallback_rate=(outcome.result or {}).get("costs", {}).get("fallback_rate"),
        )
        state.append_event(run_id, "info", "complete")
        if current and current.get("experiment_id"):
            state.bump_experiment(current["experiment_id"])
        return 0
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        current = state.get_run(run_id)
        if current and current["status"] == "cancelled":
            state.append_event(run_id, "warning", "cancelled")
            return 0
        state.update_run(run_id, status="failed", error=f"{exc}\n{tb[-4000:]}")
        state.append_event(run_id, "error", f"{type(exc).__name__}: {exc}")
        if current and current.get("experiment_id"):
            state.bump_experiment(current["experiment_id"])
        try:
            (__import__("backend.config", fromlist=["RUNS_DIR"]).RUNS_DIR / run_id).mkdir(parents=True, exist_ok=True)
            (__import__("backend.config", fromlist=["RUNS_DIR"]).RUNS_DIR / run_id / "log.txt").write_text(tb)
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
