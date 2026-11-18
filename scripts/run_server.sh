#!/usr/bin/env bash
# Run the API on the loopback interface only.
#
# Exposure is `tailscale serve --bg --https=443 http://127.0.0.1:$HTF_PORT`;
# see docs/DEPLOY.md.  This script refuses any non-loopback bind address and a
# missing data root, so a typo cannot publish the dashboard.
#
#   HTF_DATA_ROOT=/path/to/data scripts/run_server.sh
#
# Environment: HTF_HOST (127.0.0.1), HTF_PORT (8787), HTF_APP_TOKEN (optional),
# HTF_DATA_ROOT (default /root/FundedBetBacktesting/data), HTF_VAR (default var/).
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${HTF_HOST:-127.0.0.1}"
PORT="${HTF_PORT:-8787}"
DATA_ROOT="${HTF_DATA_ROOT:-/root/FundedBetBacktesting/data}"

case "$HOST" in
  127.0.0.1 | localhost | ::1) ;;
  0.0.0.0 | :: | "" | *)
    echo "refusing to bind '${HOST}': the API must listen on loopback (127.0.0.1) only" >&2
    echo "expose it with: tailscale serve --bg --https=443 http://127.0.0.1:${PORT}" >&2
    exit 2
    ;;
esac

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
  echo "invalid HTF_PORT: ${PORT}" >&2
  exit 2
fi

if [[ ! -d "$DATA_ROOT" ]]; then
  echo "data root not found: ${DATA_ROOT}" >&2
  echo "set HTF_DATA_ROOT to the sibling research data directory (see docs/DATA_REGISTER.md)" >&2
  exit 3
fi

if [[ ! -f "${DATA_ROOT}/polymarket/sii/markets.parquet" ]]; then
  echo "warning: ${DATA_ROOT}/polymarket/sii/markets.parquet is missing; API data routes will report unavailable" >&2
fi

echo "serving on http://${HOST}:${PORT} (data root ${DATA_ROOT})" >&2
exec uv run uvicorn backend.main:app --host "$HOST" --port "$PORT"
