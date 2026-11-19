#!/usr/bin/env bash
# Promote a tested commit into /opt/hermit/prod and restart the API.
#
#   deploy/promote.sh                 # origin/master
#   deploy/promote.sh <commit-or-tag> # rollback or pinned release
#
# The production checkout is detached at the target commit; var/ lives outside
# the checkout and is never touched. Run from any checkout.
set -euo pipefail

PROD=/opt/hermit/prod
TARGET="${1:-origin/master}"

cd "$PROD"
git fetch origin
HASH="$(git rev-parse "$TARGET")"
PREVIOUS="$(git rev-parse HEAD)"
echo "prod: ${PREVIOUS} -> ${HASH} (${TARGET})"

git checkout --detach "$HASH"
uv sync --frozen
(cd frontend && npm ci --no-audit --no-fund && npm run build)

systemctl restart hermit-frontend
for _ in $(seq 1 20); do
  if curl -fsS -m 2 http://127.0.0.1:8787/api/health >/dev/null; then
    echo "prod healthy at ${HASH}"
    echo "rollback: deploy/promote.sh ${PREVIOUS}"
    exit 0
  fi
  sleep 1
done

echo "prod did not become healthy; previous commit: ${PREVIOUS}" >&2
systemctl status hermit-frontend --no-pager >&2 || true
exit 1
