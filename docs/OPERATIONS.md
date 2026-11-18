# Operations

How to start, stop, inspect and back up the dashboard on the one host. The
deployment layout is in [`DEPLOY.md`](DEPLOY.md); the design boundary is in
[`BRIEF.md`](BRIEF.md) §2 and §13.

## At a glance

| Item | Value |
|---|---|
| Bind | `127.0.0.1:8787` (never `0.0.0.0`) |
| Public URL | none; Tailscale Serve only |
| Unit | `hermit-frontend.service` |
| App code | `/root/HermitTradingFrontend` |
| Read-only data | `/root/FundedBetBacktesting/data` (`HTF_DATA_ROOT`) |
| Generated state | `/root/HermitTradingFrontend/var` (`HTF_VAR`) |
| Optional auth | `HTF_APP_TOKEN`, environment only |
| Run concurrency | 1 |

## Start, stop, restart

```bash
sudo systemctl status hermit-frontend
sudo systemctl start hermit-frontend
sudo systemctl stop hermit-frontend
sudo systemctl restart hermit-frontend
```

`stop` sends SIGTERM to the API; the API terminates its worker process groups,
and `KillMode=mixed` gives the unit `TimeoutStopSec=30` before SIGKILL. A
worker that somehow survives is terminated and its run marked failed on the
next start.

Foreground debugging (same loopback bind):

```bash
cd /root/HermitTradingFrontend
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

Do not add `--host 0.0.0.0`, `--reload` in production, or a second public
listener.

## Logs

```bash
journalctl -u hermit-frontend -f
journalctl -u hermit-frontend --since "1 hour ago"
```

Per-run output:

- `var/logs/<run_id>.log` — worker stdout/stderr from the queue;
- `var/runs/<run_id>/log.txt` — the run's own log;
- `var/runs/<run_id>/diagnostics.json` and `manifest.json` — structured detail.

SSE stream while a run is active:

```bash
curl -N http://127.0.0.1:8787/api/backtests/<run_id>/events
```

## Health and data-version checks

```bash
curl -fsS http://127.0.0.1:8787/api/health | jq
curl -fsS http://127.0.0.1:8787/api/meta | jq
curl -fsS http://127.0.0.1:8787/api/cache/stats | jq
```

`/api/health` reports `code_version`, `nautilus_version` and the current
`data_version`. `/api/meta` reports coverage and the book-profile rule. Every
result page shows the run hash, the data version and the execution profile.

Check the registered sources after a restore:

```bash
cd /root/HermitTradingFrontend
uv run python scripts/data_register.py verify
uv run python scripts/data_register.py list
```

`verify` must report every path present and within 10% of its registered size.
A missing path or a size drift is a real problem: restore the source or rebuild
the derived artifact; never edit `size_bytes` to make the check pass. The raw
sibling paths are read-only and must never be written, moved or deleted.

`var/data_versions.json` is only a fingerprint cache. Deleting it forces a
rescan; it is not source data.

## Orphan and stale-run behaviour

- **API restarted while a worker lived:** the worker process group is
  terminated and the run becomes `failed` with an orphan message.
- **API restarted while a worker was gone:** a queued/running run becomes
  `failed` ("worker process was lost").
- **Completed run whose data version no longer matches the current catalog:**
  the API reports `orphaned`. It is shown, never silently re-run. Re-submit it
  deliberately if that is what you want.
- **Cancellation:** SIGTERM to the process group, then SIGKILL after the grace
  period. A cancelled run that already wrote artifacts keeps them for
  inspection.

## Backup and restore

`var/` is the only thing worth backing up. It contains `app.db`, bars, the
catalog, lifecycle/settlement indexes and run artifacts; it contains no
credentials and no sibling data.

Stop the service for a consistent copy:

```bash
sudo systemctl stop hermit-frontend
sudo tar -C /root -czf /root/hermit-var-$(date -u +%Y%m%dT%H%M%SZ).tar.gz \
  HermitTradingFrontend/var
sudo systemctl start hermit-frontend
```

Restore into an empty or stopped deployment:

```bash
sudo systemctl stop hermit-frontend
sudo tar -C /root -xzf /root/hermit-var-<timestamp>.tar.gz
sudo systemctl start hermit-frontend
uv run python scripts/data_register.py verify
```

Do not back up or copy `/root/FundedBetBacktesting/data` into this repository.
Keep backups off the public internet; they contain research results.

## Tailnet-only rule

```bash
ss -ltnp | grep 8787        # must show 127.0.0.1:8787
tailscale serve status      # must show the tailnet URL, never funnel
```

- Expose with [`deploy/tailscale-serve.sh`](../deploy/tailscale-serve.sh) or the
  equivalent `tailscale serve --bg --https=443 http://127.0.0.1:8787`.
- Never run `tailscale funnel`.
- Never bind `0.0.0.0` or add a public reverse proxy.
- If a public rule exists by mistake, remove it with `tailscale serve reset` and
  re-check the two commands above.
- The optional bearer token is defence in depth, not a substitute for the
  tailnet boundary. Set it with a systemd drop-in (`systemctl edit
  hermit-frontend`), or with an `EnvironmentFile`, and never commit it.

## Upgrade

```bash
cd /root/HermitTradingFrontend
git pull --ff-only
uv sync
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart hermit-frontend
curl -fsS http://127.0.0.1:8787/api/health | jq
```

If the data version changes, old completed runs are reported as `orphaned`;
that is expected. Re-run only the experiments you intend to repeat.

## Troubleshooting

| Symptom | Check |
|---|---|
| unit will not start | `journalctl -u hermit-frontend -n 100`; confirm `.venv/bin/uvicorn` exists and `npm run build` produced `frontend/dist` |
| UI loads but API 500s | `/api/health`; `var/` permissions; the sibling data root exists |
| run stuck queued | `var/logs/<run_id>.log`; queue concurrency is 1, so check a running run first |
| run failed after restart | expected orphan reap; inspect `error` and re-submit if intended |
| "no bars" for an instrument | build the bar file with `scripts/build_bars.py`; an empty interval is a real gap, not a bug |
| Tailscale URL 502 | the app must be healthy on `127.0.0.1:8787` first; re-run the Serve helper |
