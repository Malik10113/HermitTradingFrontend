#!/usr/bin/env bash
# Domain-scoped test runner. See TESTING.md.
#
#   scripts/test_domains.sh --changed [--fast]
#   scripts/test_domains.sh --domain engine
#   scripts/test_domains.sh --domain engine --fast
#   scripts/test_domains.sh --all
#
# Exit: 0 nothing to run or all passed, 1 tests failed, 2 bad usage.
set -uo pipefail

cd "$(dirname "$0")/.."

ALL_DOMAINS=(data indicators engine api frontend e2e tooling)
FAST=0
MODE=""
SELECTED=()

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while (($#)); do
  case "$1" in
    --changed) MODE=changed ;;
    --all) MODE=all ;;
    --domain)
      MODE=domain
      shift
      [[ $# -gt 0 ]] || usage
      case "$1" in
        data|indicators|engine|api|frontend|e2e|tooling) ;;
        *) echo "unknown domain: $1" >&2; usage ;;
      esac
      SELECTED+=("$1")
      ;;
    --fast) FAST=1 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
  shift
done

[[ -n "$MODE" ]] || usage

changed_files() {
  { git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | sort -u
}

owns() {
  local domain="$1" path="$2"
  case "$domain" in
    data) [[ "$path" == backend/data/* || "$path" == scripts/build_bars.py || "$path" == scripts/build_catalog.py || "$path" == scripts/build_match_lifecycle.py ]] ;;
    indicators) [[ "$path" == backend/indicators/* ]] ;;
    engine) [[ "$path" == backend/engine/* || "$path" == backend/strategies/* || "$path" == backend/rules/* ]] ;;
    api) [[ "$path" == backend/api/* || "$path" == backend/jobs/* || "$path" == backend/main.py || "$path" == backend/config.py ]] ;;
    frontend) [[ "$path" == frontend/src/* || "$path" == frontend/package.json || "$path" == frontend/vite.config.* || "$path" == frontend/tsconfig*.json ]] ;;
    e2e) [[ "$path" == tests/e2e/* || "$path" == frontend/e2e/* || "$path" == frontend/playwright* ]] ;;
    tooling) [[ "$path" == scripts/* || "$path" == pyproject.toml || "$path" == docs/* || "$path" == AGENTS.md || "$path" == TESTING.md ]] ;;
    *) return 1 ;;
  esac
}

dependents() {
  case "$1" in
    data) echo "indicators engine api e2e" ;;
    indicators) echo "engine api e2e" ;;
    engine) echo "api e2e" ;;
    api) echo "e2e" ;;
    frontend) echo "e2e" ;;
    e2e) echo "" ;;
    tooling) echo "" ;;
  esac
}

map_changed() {
  local files
  files="$(changed_files)"
  local list=()
  local unknown=()
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local matched=0
    for d in "${ALL_DOMAINS[@]}"; do
      if owns "$d" "$f"; then
        list+=("$d")
        matched=1
        break
      fi
    done
    [[ $matched -eq 0 ]] && unknown+=("$f")
  done <<< "$files"
  if ((${#unknown[@]})); then
    echo "Unattributed paths (routed to tooling):" >&2
    printf '  %s\n' "${unknown[@]}" >&2
    list+=(tooling)
  fi
  # add transitive dependents
  local out=("${list[@]}")
  for d in "${list[@]}"; do
    for dep in $(dependents "$d"); do
      out+=("$dep")
    done
  done
  printf '%s\n' "${out[@]}" | awk 'NF' | sort -u
}

run_domain() {
  local domain="$1"
  local failed=0
  local skipped=0
  echo "== domain: $domain"
  case "$domain" in
    frontend)
      if [[ -d frontend/node_modules ]]; then
        if [[ $FAST -eq 1 ]]; then
          (cd frontend && npm test -- --run --reporter=dot) || failed=1
        else
          (cd frontend && npm test -- --run) || failed=1
        fi
      else
        echo "   BLOCKED: frontend/node_modules absent (run npm install)"
        skipped=1
      fi
      ;;
    e2e)
      if [[ -n "${HTF_E2E:-}" ]]; then
        (cd frontend && npx playwright test) || failed=1
      else
        echo "   BLOCKED: set HTF_E2E=1 to run Playwright"
        skipped=1
      fi
      ;;
    *)
      if [[ -d "tests/$domain" ]] && find "tests/$domain" -name 'test_*.py' -o -name '*_test.py' | grep -q .; then
        if [[ $FAST -eq 1 ]]; then
          (uv run pytest "tests/$domain" -q -x --no-header -m 'not slow') || failed=1
        else
          (uv run pytest "tests/$domain" -q) || failed=1
        fi
      else
        echo "   BLOCKED: no tests found for domain '$domain'"
        skipped=1
      fi
      ;;
  esac
  if [[ $failed -ne 0 ]]; then
    return 1
  fi
  if [[ $skipped -ne 0 ]]; then
    return 2
  fi
  return 0
}

case "$MODE" in
  changed)
    mapfile -t SELECTED < <(map_changed)
    echo "Domains for changed paths: ${SELECTED[*]:-none}"
    ;;
  all)
    SELECTED=("${ALL_DOMAINS[@]}")
    ;;
esac

[[ ${#SELECTED[@]} -gt 0 ]] || { echo "nothing to run"; exit 0; }

STATUS=0
PASSED=()
BLOCKED=()
FAILED=()
for d in "${SELECTED[@]}"; do
  run_domain "$d"
  result=$?
  case "$result" in
    0) PASSED+=("$d") ;;
    1) FAILED+=("$d"); STATUS=1 ;;
    2) BLOCKED+=("$d"); [[ $STATUS -eq 0 ]] && STATUS=2 ;;
  esac
done
echo "== summary: passed=${PASSED[*]:-none}; blocked=${BLOCKED[*]:-none}; failed=${FAILED[*]:-none}"
exit $STATUS
