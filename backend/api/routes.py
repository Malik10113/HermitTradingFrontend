"""FastAPI routes for the v1 API surface."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.api import services
from backend.api.errors import ApiError, bad_request, not_found
from backend.config import RUNS_DIR, SETTINGS, code_version, nautilus_version
from backend.data.versions import run_data_version
from backend.engine.artifacts import parquet_rows, read_artifact, read_json_artifact
from backend.jobs import state
from backend.jobs.queue import QUEUE
from backend.rules.schema import (
    EvaluationConfig,
    ExecutionConfig,
    RiskConfig,
    RunConfig,
    StrategySection,
    run_hash,
    split_hash,
    strategy_hash,
)

router = APIRouter(prefix="/api")


def token_authorized(authorization: str | None) -> bool:
    """Whether a request may proceed; true when no token is configured."""
    expected = SETTINGS.app_token
    if not expected:
        return True
    supplied = ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = value.strip()
    return bool(supplied) and secrets.compare_digest(supplied, expected)


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Require ``Authorization: Bearer <HTF_APP_TOKEN>`` when a token is set.

    Optional by design: the app is tailnet-only, and the token adds a second
    lock for hosts whose tailnet includes devices the owner does not control.
    Every ``/api`` route, including health, is protected; static assets are
    served outside this router and stay public so the app can boot.
    """
    if not token_authorized(authorization):
        raise ApiError(401, "unauthorized", "missing or invalid bearer token", {"scheme": "Bearer"})


router.dependencies = [Depends(require_token)]


class PreviewRequest(BaseModel):
    instrument_id: str
    bar_type: str = "1m"
    start: str | None = Field(default=None, alias="from")
    end: str | None = Field(default=None, alias="to")
    indicators: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class PreflightRequest(BaseModel):
    instrument_id: str
    bar_type: str = "1m"
    start: str | None = Field(default=None, alias="from")
    end: str | None = Field(default=None, alias="to")
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    model_config = {"populate_by_name": True}


class PresetRequest(BaseModel):
    name: str
    config: dict[str, Any]


class SaveStrategyRequest(BaseModel):
    name: str
    config: StrategySection


class RunRequest(RunConfig):
    preset_id: str | None = None


class ExperimentAxis(BaseModel):
    path: str
    values: list[Any]


class ExperimentRequest(BaseModel):
    name: str
    base: RunConfig
    axes: list[ExperimentAxis]

    model_config = {"extra": "forbid"}


def _terminal(status: str) -> bool:
    return status in ("complete", "failed", "cancelled", "orphaned")


def _trade_rows(run_id: str) -> list[dict[str, Any]]:
    rows = parquet_rows(run_id, "trades.parquet", 100_000)
    out = []
    for i, t in enumerate(rows):
        intent = "open_long" if t.get("side") == "long" else "open_short"
        out.append(
            {
                **t,
                "trade_id": f"T-{i + 1:04d}",
                "side": "BUY",
                "close_side": "SELL",
                "intent": intent,
                "contracts": t.get("quantity"),
                "held_to_settlement": t.get("exit_reason") == "settlement",
                "phase": t.get("entry_phase"),
            }
        )
    return out


def _signal_rows(run_id: str) -> list[dict[str, Any]]:
    rows = parquet_rows(run_id, "signals.parquet", 100_000)
    out = []
    for s in rows:
        action = str(s.get("action", "signal"))
        ts_ns = s.get("ts_ns")
        ts = None
        if ts_ns:
            from datetime import datetime, timezone

            ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        out.append(
            {
                **s,
                "ts": ts,
                "kind": "signal",
                "side": "BUY" if action.endswith("entry") else "SELL",
                "reason": action,
            }
        )
    return out


def _run_payload(run: dict[str, Any], include_summary: bool = True, detail: bool = False) -> dict[str, Any]:
    run_id = run["run_id"]
    manifest = read_json_artifact(run_id, "manifest.json") if include_summary else None
    summary = read_json_artifact(run_id, "summary.json") if include_summary else None
    config = run.get("config") or {}
    split = (config.get("evaluation") or {}).get("split")
    holdout = None
    if split:
        try:
            sh = split_hash(RunConfig.model_validate(config))
            holdout = state.holdout_summary(
                (config.get("strategy") or {}).get("instrument_id", ""),
                run["strategy_hash"],
                sh or "",
            )
            holdout["split"] = split
            holdout["revealed"] = (run.get("holdout_reveals") or 0) > 0
        except Exception:  # noqa: BLE001
            holdout = None
    status = run["status"]
    orphaned = False
    current = run_data_version()
    if status == "complete" and run.get("data_version") and run["data_version"] != current:
        orphaned = True
    if orphaned:
        status = "orphaned"
    payload = {
        "run_id": run_id,
        "status": status,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "config": config,
        "strategy_hash": run.get("strategy_hash"),
        "run_hash": run.get("run_hash"),
        "data_version": run.get("data_version"),
        "requested_profile": run.get("requested_profile"),
        "effective_profile": run.get("effective_profile"),
        "fallback_reason": run.get("fallback_reason"),
        "fallback_rate": run.get("fallback_rate"),
        "error": run.get("error"),
        "holdout": holdout,
        "experiment_id": run.get("experiment_id"),
    }
    if include_summary:
        # Out-of-sample metrics stay hidden until an explicit reveal. Overall
        # metrics would leak the test window, so an unrevealed split reports
        # the in-sample slice only.
        if summary and split and not (holdout or {}).get("revealed"):
            in_sample = summary.get("in_sample")
            if in_sample is not None:
                summary = {**in_sample, "in_sample": in_sample, "out_of_sample": None, "oos_hidden": True}
                payload["metrics_scope"] = "in_sample"
            else:
                summary = {**summary, "oos_hidden": True}
                payload["metrics_scope"] = "in_sample"
        elif summary:
            payload["metrics_scope"] = "all"
        payload["summary"] = summary
        payload["manifest"] = manifest
        payload["metrics"] = summary
        payload["phase_breakdown"] = (manifest or {}).get("phase_breakdown") if manifest else None
        payload["artifacts"] = [
            {"name": n, "sha256": h}
            for n, h in ((manifest or {}).get("artifacts") or {}).items()
        ]
        if detail:
            payload["execution"] = (manifest or {}).get("execution")
            payload["lifecycle_version"] = (manifest or {}).get("lifecycle_version")
            payload["diagnostics"] = read_json_artifact(run_id, "diagnostics.json")
            costs = parquet_rows(run_id, "costs.parquet", 1)
            payload["costs"] = costs[0] if costs else None
            if payload["costs"] is not None:
                payload["costs"]["requested_profile"] = (manifest or {}).get("execution", {}).get("requested")
                payload["costs"]["effective_profile"] = (manifest or {}).get("execution", {}).get("effective")
                payload["costs"]["fallback_reason"] = (manifest or {}).get("execution", {}).get("fallback_reason")
            payload["equity"] = parquet_rows(run_id, "equity.parquet", 100_000)
            payload["trades"] = _trade_rows(run_id)
            payload["signals"] = _signal_rows(run_id)
            phases = payload.get("phase_breakdown") or {}
            payload["phases"] = [
                {
                    "phase": phase,
                    "trades": metrics.get("trade_count"),
                    "pnl_usd": metrics.get("net_pnl_usd"),
                    "return_pct": metrics.get("return_pct"),
                }
                for phase, metrics in phases.items()
            ]
    return payload


# --------------------------------------------------------------------- meta


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "code_version": code_version(),
        "nautilus_version": nautilus_version(),
        "data_version": run_data_version(),
    }


@router.get("/meta")
def meta() -> dict[str, Any]:
    base = services.meta()
    base["datasets"] = [
        {"name": "polymarket_tape", "kind": "raw", "path": "polymarket/{football,epl}/*_moneyline_tape.parquet"},
        {"name": "polymarket_markets", "kind": "raw", "path": "results/polymarket_markets.parquet"},
        {"name": "espn_match_lifecycle", "kind": "derived", "path": "var/lifecycle/match_lifecycle.parquet"},
        {"name": "settlement", "kind": "derived", "path": "var/settlement/settlement.parquet"},
    ]
    base["book_profile"] = "2026 only; falls back to conservative when coverage is absent"
    return base


# --------------------------------------------------------------- instruments


@router.get("/instruments")
def instruments(
    q: str | None = None,
    league: str | None = None,
    limit: int = Query(default=50, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return services.list_instruments(q, league, limit, offset)


@router.get("/instruments/{instrument_id}")
def instrument(instrument_id: str) -> dict[str, Any]:
    out = services.get_instrument(instrument_id)
    out["lifecycle"] = services.lifecycle(instrument_id)
    out["settlement"] = services.settlement_for(instrument_id)
    return out


@router.get("/instruments/{instrument_id}/quality")
def instrument_quality(
    instrument_id: str,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    return services.quality(instrument_id, start, end)


@router.get("/search")
def search(q: str = Query(min_length=1), limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    out = services.list_instruments(q, None, limit, 0)
    return {"query": q, "items": out["items"], "instruments": out["items"], "total": out["total"]}


# ----------------------------------------------------------------------- bars


@router.get("/bars")
def bars(
    instrument_id: str,
    bar_type: str = "1m",
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=20_000, ge=1, le=100_000),
) -> dict[str, Any]:
    return services.bars(instrument_id, bar_type, start, end, limit)


# ----------------------------------------------------------------- indicators


@router.get("/indicators")
def indicators() -> dict[str, Any]:
    return services.indicator_registry()


@router.post("/indicators/preview")
def indicator_preview(req: PreviewRequest) -> dict[str, Any]:
    return services.indicator_preview(req.model_dump(by_alias=True))


# ------------------------------------------------------------------ execution


@router.post("/execution/preflight")
def execution_preflight(req: PreflightRequest) -> dict[str, Any]:
    return services.preflight(req.model_dump(by_alias=True))


# ----------------------------------------------------------------- strategies


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    from backend.strategies.builtins import catalogue

    return {"builtins": catalogue(), "saved": state.list_strategies()}


@router.post("/strategies", status_code=201)
def save_strategy(req: SaveStrategyRequest) -> dict[str, Any]:
    sid = "str_" + uuid.uuid4().hex[:12]
    state.save_strategy(sid, req.name, req.config.model_dump(mode="json", exclude_none=True))
    return {"id": sid, "name": req.name}


# -------------------------------------------------------------------- presets


@router.get("/presets")
def presets() -> dict[str, Any]:
    items = state.list_presets()
    return {"items": items, "presets": items}


@router.post("/presets", status_code=201)
def create_preset(req: PresetRequest) -> dict[str, Any]:
    pid = "pre_" + uuid.uuid4().hex[:12]
    state.create_preset(pid, req.name, req.config)
    return {"id": pid, "name": req.name}


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: str) -> None:
    if not state.delete_preset(preset_id):
        raise not_found("preset", preset_id)


# ------------------------------------------------------------------- backtests


def _prepare(req: RunRequest) -> dict[str, Any]:
    from backend.engine.runner import prepare_run

    strategy = req.strategy
    if strategy.builtin_id and not any(
        (strategy.long_entry, strategy.long_exit, strategy.short_entry, strategy.short_exit)
    ):
        from backend.strategies.builtins import build as build_builtin

        rules = build_builtin(
            strategy.builtin_id,
            strategy.instrument_id,
            strategy.bar_type,
            strategy.builtin_params,
        )
        if strategy.direction:
            rules["direction"] = strategy.direction
        strategy = StrategySection.model_validate(rules)
    config = RunConfig(
        strategy=strategy,
        execution=req.execution,
        risk=req.risk,
        evaluation=req.evaluation,
    )
    return prepare_run(config)


@router.post("/backtests", status_code=202)
def create_backtest(req: RunRequest) -> dict[str, Any]:
    prepared = _prepare(req)
    run_id = "run_" + uuid.uuid4().hex[:16]
    state.create_run(
        run_id,
        prepared["config"],
        prepared["strategy_hash"],
        prepared["run_hash"],
        prepared["data_version"],
        prepared["requested_profile"],
        prepared["effective_profile"],
        prepared.get("fallback_reason"),
        experiment_id=prepared.get("experiment_id"),
    )
    state.append_event(run_id, "info", "queued")
    QUEUE.submit(run_id)
    return {
        "run_id": run_id,
        "status": "queued",
        "run_hash": prepared["run_hash"],
        "strategy_hash": prepared["strategy_hash"],
        "requested_profile": prepared["requested_profile"],
        "effective_profile": prepared["effective_profile"],
        "fallback_reason": prepared.get("fallback_reason"),
    }


@router.get("/backtests")
def list_backtests(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    runs = state.list_runs(limit=limit, offset=offset)
    items = [_run_payload(r, include_summary=True, detail=False) for r in runs]
    return {"items": items, "runs": items, "total": len(runs)}


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str) -> dict[str, Any]:
    run = state.get_run(run_id)
    if run is None:
        raise not_found("run", run_id)
    return _run_payload(run, detail=True)


@router.get("/backtests/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Server-sent status and log frames for one run.

    Two frames are emitted per status transition: a named ``status`` event for
    listeners that add an event listener, and a default-message ``data:`` frame
    so a plain ``EventSource.onmessage`` handler sees it too.  The stream ends
    with an ``end`` event on a terminal status, including when the run already
    finished before the client connected, so a fast run cannot lose it.
    """
    if state.get_run(run_id) is None:
        raise not_found("run", run_id)

    async def stream():
        last = 0
        last_status = None
        idle = 0.0
        while True:
            current = state.get_run(run_id)
            status = current["status"] if current else "failed"
            evs = state.events(run_id, after=last)
            for e in evs:
                last = max(last, int(e["id"]))
                yield f"id: {e['id']}\nevent: log\ndata: {json.dumps(e, sort_keys=True)}\n\n"
            if status != last_status:
                last_status = status
                payload = json.dumps({"status": status}, sort_keys=True)
                yield f"event: status\ndata: {payload}\n\n"
                # Default-message frame so a plain EventSource.onmessage sees it.
                yield f"data: {payload}\n\n"
            if _terminal(status):
                yield f"event: end\ndata: {json.dumps({'status': status}, sort_keys=True)}\n\n"
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/backtests/{run_id}/artifacts/{name}")
def artifact(
    run_id: str,
    name: str,
    format: str | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=100_000),
):
    path = read_artifact(run_id, name)
    if path is None:
        raise not_found("artifact", f"{run_id}/{name}")
    if format == "json" and name.endswith(".parquet"):
        return {"run_id": run_id, "name": name, "rows": parquet_rows(run_id, name, limit)}
    return FileResponse(path)


@router.post("/backtests/{run_id}/reveal-holdout")
def reveal_holdout(run_id: str) -> dict[str, Any]:
    run = state.get_run(run_id)
    if run is None:
        raise not_found("run", run_id)
    if run["status"] != "complete":
        raise bad_request("not_complete", "only a completed run can reveal out-of-sample results", {"status": run["status"]})
    config = run.get("config") or {}
    split = (config.get("evaluation") or {}).get("split")
    if not split:
        raise bad_request("no_split", "this run has no train/test split")
    sh = split_hash(RunConfig.model_validate(config))
    universe = (config.get("strategy") or {}).get("instrument_id", "")
    result = state.record_reveal(universe, run["strategy_hash"], sh or "", run_id)
    result["revealed"] = True
    return result


@router.delete("/backtests/{run_id}", status_code=204)
def delete_backtest(run_id: str) -> None:
    run = state.get_run(run_id)
    if run is None:
        raise not_found("run", run_id)
    # Stop the worker before removing its state, so it cannot keep writing into
    # a run directory that is about to disappear.
    if run["status"] in ("queued", "running"):
        QUEUE.cancel(run_id)
    state.delete_run(run_id)
    shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)


@router.get("/runs/compare")
def compare(ids: str = Query(min_length=1)) -> dict[str, Any]:
    run_ids = [x for x in ids.split(",") if x]
    if not 2 <= len(run_ids) <= 3:
        raise bad_request("bad_compare", "compare requires 2 or 3 run ids")
    out = []
    for run_id in run_ids:
        run = state.get_run(run_id)
        if run is None:
            raise not_found("run", run_id)
        payload = _run_payload(run)
        eq = parquet_rows(run_id, "equity.parquet", 100_000)
        base = None
        norm = []
        for point in eq:
            v = point.get("equity_usd")
            if v is None:
                continue
            if base is None and v:
                base = v
            norm.append({"ts": point.get("ts"), "equity_index": (v / base * 100.0) if base else None})
        payload["equity_index"] = norm
        out.append(payload)
    return {"runs": out, "base": 100.0}


# ---------------------------------------------------------------- experiments


@router.get("/experiments")
def experiments() -> dict[str, Any]:
    items = state.list_experiments()
    return {"items": items, "experiments": items}


def _set_path(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


@router.post("/experiments", status_code=202)
def create_experiment(req: ExperimentRequest) -> dict[str, Any]:
    if not 1 <= len(req.axes) <= 2:
        raise bad_request("bad_grid", "one or two axes are supported")
    combos: list[dict[str, Any]] = [{}]
    for axis in req.axes:
        nxt = []
        for combo in combos:
            for value in axis.values:
                c = dict(combo)
                c[axis.path] = value
                nxt.append(c)
        combos = nxt
    if not 1 <= len(combos) <= SETTINGS.hard_max_grid_runs:
        raise bad_request("grid_too_large", f"grid has {len(combos)} variants; hard cap is {SETTINGS.hard_max_grid_runs}")
    if len(combos) > SETTINGS.max_grid_runs:
        raise bad_request("grid_too_large", f"grid has {len(combos)} variants; default cap is {SETTINGS.max_grid_runs}")
    experiment_id = "exp_" + uuid.uuid4().hex[:12]
    state.create_experiment(experiment_id, req.name, req.model_dump(mode="json"), len(combos))
    created = []
    for combo in combos:
        data = req.base.model_dump(mode="json", exclude_none=True)
        for path, value in combo.items():
            _set_path(data, path, value)
        try:
            prepared = _prepare(RunRequest.model_validate(data))
        except Exception as exc:  # noqa: BLE001
            state.update_experiment(experiment_id, status="failed")
            raise bad_request("bad_variant", str(exc), {"variant": combo}) from exc
        run_id = "run_" + uuid.uuid4().hex[:16]
        state.create_run(
            run_id,
            prepared["config"],
            prepared["strategy_hash"],
            prepared["run_hash"],
            prepared["data_version"],
            prepared["requested_profile"],
            prepared["effective_profile"],
            prepared.get("fallback_reason"),
            experiment_id=experiment_id,
        )
        created.append(run_id)
        QUEUE.submit(run_id)
    state.update_experiment(experiment_id, status="running")
    return {"experiment_id": experiment_id, "run_ids": created, "total": len(created)}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict[str, Any]:
    exp = state.get_experiment(experiment_id)
    if exp is None:
        raise not_found("experiment", experiment_id)
    children = exp.get("runs") or []
    exp["runs"] = [
        _run_payload(r, include_summary=True, detail=False) if r.get("run_id") else r
        for r in children
    ]
    done = len([r for r in children if _terminal(r["status"])])
    exp["completed"] = done
    if done == len(children) and children:
        if all(r["status"] == "complete" for r in children):
            exp["status"] = "complete"
        elif exp.get("status") == "cancelled" or all(r["status"] == "cancelled" for r in children):
            exp["status"] = "cancelled"
        else:
            exp["status"] = "failed"
    return exp


@router.post("/experiments/{experiment_id}/cancel")
def cancel_experiment(experiment_id: str) -> dict[str, Any]:
    exp = state.get_experiment(experiment_id)
    if exp is None:
        raise not_found("experiment", experiment_id)
    cancelled = []
    pending = []
    for child in exp.get("runs") or []:
        if _terminal(child["status"]):
            continue
        # Mark every child first: stopping a running worker releases a queue
        # slot, and a still-queued sibling must not be able to claim it.
        if QUEUE.request_cancel(child["run_id"]):
            pending.append(child["run_id"])
        cancelled.append(child["run_id"])
    for run_id in pending:
        QUEUE.stop(run_id)
    state.bump_experiment(experiment_id)
    # bump_experiment derives complete/failed from the children; a cancelled
    # parent stays cancelled even if some children had already completed.
    state.update_experiment(experiment_id, status="cancelled")
    after = state.get_experiment(experiment_id) or {}
    return {
        "experiment_id": experiment_id,
        "cancelled": cancelled,
        "completed": after.get("completed"),
        "total": after.get("total"),
        "status": after.get("status"),
    }


@router.get("/cache/stats")
def cache_stats() -> dict[str, Any]:
    from backend.engine.signals import cache_stats as signal_stats
    from backend.indicators.cache import cache_stats as stats

    indicator = stats()
    signal = signal_stats()
    # Flat keys stay the indicator cache's stats for backward compatibility;
    # the per-cache blocks are additive.
    return {**indicator, "indicator": indicator, "signal": signal}
