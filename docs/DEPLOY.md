# Deployment (tailnet only)

The app binds `127.0.0.1:8787`. Tailscale Serve terminates HTTPS on the tailnet
and proxies to that loopback listener. No public port, no Funnel.

## 1. Build

```bash
uv sync
cd frontend && npm ci && npm run build && cd ..
uv run python scripts/build_match_lifecycle.py
```

## 2. Run under systemd

Create `/etc/systemd/system/hermit-frontend.service`:

```ini
[Unit]
Description=Hermit Trading Frontend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/HermitTradingFrontend
Environment=HTF_HOST=127.0.0.1
Environment=HTF_PORT=8787
# Optional shared secret, read from the environment only:
# Environment=HTF_APP_TOKEN=change-me
ExecStart=/root/HermitTradingFrontend/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=3
# Workers are process-isolated; give them room to finish a run.
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermit-frontend
sudo systemctl status hermit-frontend
```

On startup the API reaps queued/running runs whose worker process is gone and
marks them failed; a completed run whose data version is no longer present is
shown as `orphaned`, never silently re-run.

## 3. Expose with Tailscale Serve

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
reverse proxy with a public interface.

## 4. Backups

Only `var/` needs backing up if you want run history. It is gitignored and
contains no credentials. The sibling data directory is read-only and is never
copied into the repository.
