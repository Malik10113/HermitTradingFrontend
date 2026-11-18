#!/usr/bin/env bash
# Expose the loopback-only app on the tailnet. Run as the tailnet owner.
# Never use `tailscale funnel`, never bind 0.0.0.0.
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed" >&2
  exit 1
fi

if ! curl -fsS http://127.0.0.1:8787/api/health >/dev/null; then
  echo "the app is not answering on 127.0.0.1:8787; start hermit-frontend first" >&2
  exit 1
fi

tailscale serve --bg --https=443 http://127.0.0.1:8787
tailscale serve status

echo
echo "Open the https://<host>.<tailnet>.ts.net address printed above."
ss -ltnp | grep 8787 || true
