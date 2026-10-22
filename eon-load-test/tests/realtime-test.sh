#!/usr/bin/env bash
# Boots the REAL eon stack (both services) in local containers and runs the harness
# through the full matrix: single/multi process × cache on/off, plus rate-limit
# on/off. Safe by construction: RUN_CRONS=false everywhere (no migrations/crons),
# an isolated redis for cache, AI routes off. Only writes are stage session touches.
#
#   ./realtime-test.sh                 # full matrix + consolidate + bug scan
#   COMBOS="single-cache-on" ./realtime-test.sh   # a subset
set -uo pipefail
cd "$(dirname "$0")"
HARNESS="$(cd .. && pwd)"
COMPOSE="docker-compose.realtime.yml"
PROJECT=eonrt
OUT="${OUT:-$PWD/out-realtime}"
REPORTS="$OUT/reports"

command -v docker >/dev/null || { echo "✗ Docker is required"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "✗ 'docker compose' v2 is required"; exit 1; }

# Cookie from the harness .env (trim the leading space the user's value has).
COOKIE_VAL="$(sed -nE 's/^COOKIE=[[:space:]]*(.+)$/\1/p' "$HARNESS/.env" | head -1)"
[[ -n "$COOKIE_VAL" ]] || echo "⚠ No COOKIE in $HARNESS/.env — express-guardian endpoints will be skipped."

rm -rf "$OUT"; mkdir -p "$REPORTS"

# Exactly 4 combos: {single, multi} × {cache-on, cache-off}. Rate limiting is OFF in
# all of them (always disabled while load testing); cache-off disables both the
# Redis cache and the limiter via a single CACHE_ENABLED=false.
# label : replicas : CACHE_ENABLED : rps : duration
MATRIX=(
  "single-cache-on:1:true:20:25s"
  "single-cache-off:1:false:8:20s"
  "multi-cache-on:3:true:20:25s"
  "multi-cache-off:3:false:8:20s"
)
SELECT="${COMBOS:-}"

down() { docker compose -p "$PROJECT" -f "$COMPOSE" down --remove-orphans >/dev/null 2>&1 || true; }
trap down EXIT INT TERM

# fast-dragon public GET paths, read straight from the harness endpoint list so the
# warmup can't drift from what's tested.
FD_PATHS="$(bun -e 'import {PUBLIC_ENDPOINTS} from "../src/endpoints.js"; console.log(PUBLIC_ENDPOINTS.map(e=>e.path).join("\n"))' 2>/dev/null)"

warmup() { # one read-only pass so a cache-on run is measured warm, not cold
  [[ -z "$FD_PATHS" ]] && return 0
  echo "  warming cache (one read-only pass over fast-dragon endpoints)…"
  while IFS= read -r p; do
    [[ -n "$p" ]] && curl -s -o /dev/null --max-time 30 "http://localhost:8080$p" || true
  done <<<"$FD_PATHS"
}

wait_ready() { # poll the LB until fast-dragon answers 200, up to ~120s
  local url="http://localhost:8080/api/top-coins" i code
  for i in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    [[ "$code" == "200" ]] && { echo "  ✓ fast-dragon ready (HTTP 200 after ~$((i*2))s)"; return 0; }
    sleep 2
  done
  echo "  ⚠ fast-dragon not serving 200 after 120s (last code $code) — running anyway to capture it"
  return 1
}

echo "▶ Building the harness image once…"
docker build -q -t eon-load-test-k6 "$HARNESS" >/dev/null

for row in "${MATRIX[@]}"; do
  IFS=: read -r label n cache rps dur <<<"$row"
  [[ -n "$SELECT" && " $SELECT " != *" $label "* ]] && continue
  # Cache off ⇒ force-exclude the AI + portfolio group (paid upstream / 503 with no cache).
  combo_cacheoff=false; [[ "$cache" == "false" ]] && combo_cacheoff=true

  echo
  echo "════════ combo $label  (replicas=$n cache=$cache, rate-limit OFF, ${rps}rps/${dur}) ════════"
  down
  echo "▶ Starting eon: fast-dragon×$n, express-guardian×$n, redis, lb…"
  # Rate limiting OFF in every combo (always off while load testing). With cache off,
  # CACHE_ENABLED=false already disables the limiter too; RATE_LIMIT_ENABLED=false
  # keeps it off for the cache-on combos.
  CACHE_ENABLED="$cache" RATE_LIMIT_ENABLED=false \
    docker compose -p "$PROJECT" -f "$COMPOSE" up -d --build \
    --scale fast-dragon="$n" --scale express-guardian="$n" >/dev/null 2>"$OUT/$label.up.log" \
    || { echo "  ✗ compose up failed — see $OUT/$label.up.log"; continue; }
  wait_ready || true
  # Warm the cache before a cache-ON measured run so it isn't cold (RUN_CRONS is off,
  # so no warmer runs). Cache-off skips this (nothing to warm).
  [[ "$cache" == "true" ]] && warmup

  # Run the harness ON the compose network and target the LB service directly.
  echo "▶ Load (COMBO=$label)…"
  env ENV_FILE=/dev/null REPORTS_DIR="$REPORTS" HEADLESS=true LIVE_UI=false \
    DOCKER_NETWORK=eon-realtime-net COMBO="$label" \
    TARGET_BASE_URL="http://lb:8080" \
    GUARDIAN_BASE_URL="http://lb:8081" \
    COOKIE="$COOKIE_VAL" CACHE_OFF="$combo_cacheoff" \
    TEST_MODE=load REQUESTS_PER_SECOND="$rps" DURATION="$dur" \
    REQUEST_TIMEOUT=15s MAX_VUS=200 PRE_ALLOCATED_VUS=50 \
    "$HARNESS/run.sh" < /dev/null > "$OUT/$label.load.log" 2>&1
  if ls "$REPORTS/$label"/*.json >/dev/null 2>&1; then
    echo "  ✓ report: $(ls -t "$REPORTS/$label"/*.md | head -1)"
  else
    echo "  ✗ no report written — see $OUT/$label.load.log"
  fi
done

down
echo
echo "▶ Consolidating…"
REPORTS_DIR="$REPORTS" "$HARNESS/consolidate.sh" >"$OUT/consolidate.log" 2>&1 || true
ls "$REPORTS"/consolidated-*.md >/dev/null 2>&1 && echo "  ✓ $(ls -t "$REPORTS"/consolidated-*.md | head -1)"

echo
echo "▶ Bug scan…"
python3 scan.py "$REPORTS" || true
echo
echo "Done. Reports + logs: $OUT"
