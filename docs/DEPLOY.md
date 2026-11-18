# Deployment (tailnet only)

The app binds `127.0.0.1:8787`. Tailscale Serve terminates HTTPS on the tailnet
and proxies to that loopback listener. No public port, no Funnel, no
`0.0.0.0` bind.

The checked-in unit is [`deploy/hermit-frontend.service`](../deploy/hermit-frontend.service);
the checked-in exposure helper is [`deploy/tailscale-serve.sh`](../deploy/tailscale-serve.sh).
If this document and those files disagree, the files win — fix both in the same
change.

## 1. Build

```bash
cd /root/HermitTradingFrontend
uv sync
cd frontend && npm ci && npm run build && cd ..
uv run python scripts/build_match_lifecycle.py
```

Optional first load of bars and the instrument catalogue:

```bash
uv run python scripts/build_bars.py --all --limit 20
uv run python scripts/build_catalog.py --all
```

`HTF_DATA_ROOT` defaults to `/root/FundedBetBacktesting/data` and is read-only.
All generated state goes to `HTF_VAR`, default `/root/HermitTradingFrontend/var`.

## 2. Run under systemd

Install the checked-in unit:

```bash
sudo install -m 0644 deploy/hermit-frontend.service /etc/systemd/system/hermit-frontend.service
sudo systemctl daemon-reload
sudo systemctl enable --now hermit-frontend
sudo systemctl status hermit-frontend
```

The unit is:

```ini
[Unit]
Description=Hermit Trading Frontend (tailnet research dashboard)
After=network.target tailscaled.service
Wants=tailscaled.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/HermitTradingFrontend
Environment=HTF_HOST=127.0.0.1
Environment=HTF_PORT=8787
Environment=HTF_DATA_ROOT=/root/FundedBetBacktesting/data
Environment=HTF_VAR=/root/HermitTradingFrontend/var
# Optional shared secret; set it in the environment, never in git.
# Environment=HTF_APP_TOKEN=change-me
ExecStart=/root/HermitTradingFrontend/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=3
# Workers are separate process groups; allow a run to finish or be terminated.
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

On startup the API reaps queued/running runs whose worker process is gone,
terminates a worker that survived an API restart, and marks them failed. A
completed run whose data version is no longer present is shown as `orphaned`,
never silently re-run. See [`OPERATIONS.md`](OPERATIONS.md) for start/stop,
logs, data-version checks and backups.

## 3. Expose with Tailscale Serve

Use the checked-in helper after the unit is healthy:

```bash
./deploy/tailscale-serve.sh
```

It checks `/api/health`, then runs the equivalent of:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8787
tailscale serve status
```

The resulting URL is `https://<host>.<tailnet>.ts.net`. Confirm there is no
public listener:

```bash
ss -ltnp | grep 8787        # must show 127.0.0.1:8787
tailscale serve status      # must not show funnel
```

Do not run `tailscale funnel`, do not change the bind address, and do not add a
reverse proxy with a public interface. If the app was exposed publicly by
mistake, stop the serving rule with `tailscale serve reset` and re-check
`tailscale serve status`.

## 4. Backups

Only `var/` needs backing up if you want run history and caches. It is
gitignored, holds no credentials (the optional token lives in the environment),
and contains no sibling data. The sibling data directory is read-only and is
never copied into the repository. See [`OPERATIONS.md`](OPERATIONS.md) for the
backup and restore commands.
