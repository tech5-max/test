#!/usr/bin/env bash
# Implementation checks for the eon load-test switches, run against the real
# service source with Bun (the runtime the services already use). The cache +
# pool checks need no database or Redis. The RUN_CRONS / RATE_LIMIT / load-balancer
# checks need a running eon and are documented in README.md.
#   ./run.sh
set -uo pipefail
cd "$(dirname "$0")"

command -v bun >/dev/null 2>&1 || { echo "✗ Bun is required (the services run on Bun): https://bun.sh"; exit 1; }

fails=0
run() { # <label> <env assignments...>
  local label="$1"; shift
  echo "════ $label ════"
  if env "$@" bun cache-pool-check.ts 2>&1 \
       | grep -vE "Database connections configured|injected env|Connected to (PUB/SUB )?Redis|Redis connection error"; then :; fi
  # Re-run quietly just for the exit code (grep above masks it).
  env "$@" bun cache-pool-check.ts >/dev/null 2>&1 || { fails=$((fails+1)); echo "  → FAIL"; }
  echo
}

run "cache OFF · pool 25" CACHE_ENABLED=false DB_POOL_MAX=25
run "cache ON · pool default (10)" CACHE_ENABLED=true

echo "════ summary ════"
if [[ $fails -eq 0 ]]; then
  echo "✅ All switch checks passed."
  echo "   (RUN_CRONS, RATE_LIMIT_ENABLED and the multi-container LB are runtime"
  echo "    checks — see README.md to run them against a live eon.)"
  exit 0
else
  echo "❌ $fails scenario(s) failed."
  exit 1
fi
