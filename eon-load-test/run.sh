#!/usr/bin/env bash
# Runs the k6 load test in Docker (the only requirement on the host).
#   ./run.sh                     # settings from .env
#   ENV_FILE=.env.stage ./run.sh # another env file
#   TEST_MODE=load ./run.sh      # inline overrides win over the env file
#
# On a server (no terminal/display) or with HEADLESS=true: one status line every
# 10 s in the terminal. On a laptop: k6's progress bar and the live page.
# Reports: reports/<mode>-<time>.md (+ .html, .json)
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || { echo "✗ Docker is required (https://docs.docker.com/engine/install/)"; exit 1; }

ENV_FILE="${ENV_FILE:-.env}"
VARS=(TARGET_BASE_URL GUARDIAN_BASE_URL COOKIE COMBO LOAD_TEST_SERVICE TEST_MODE INCLUDE_AI_ANALYSIS CACHE_OFF
      INCLUDE_WRITE_ROUTES UUID_ROTATE_COUNT
      REQUESTS_PER_MINUTE REQUESTS_PER_SECOND DURATION
      STRESS_START_REQUESTS STRESS_STEP_REQUESTS STRESS_STEP_SECONDS STRESS_MAX_DURATION
      HEALTHY_ERROR_RATE HEALTHY_P95_MS CRITICAL_ERROR_RATE
      PRE_ALLOCATED_VUS MAX_VUS REQUEST_TIMEOUT AI_REQUEST_TIMEOUT LOG_ERRORS
      HEADLESS MONITOR_EVERY DASHBOARD_PORT)

# Load the env file without overriding variables already set in the shell.
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    key="${line%%=*}"; value="${line#*=}"
    key="$(echo "$key" | xargs)"
    [[ -z "${!key:-}" ]] && export "$key=$value"
  done < "$ENV_FILE"
else
  echo "▶ No $ENV_FILE found; using the environment and defaults (copy .env.example to .env)."
fi

# k6 runs inside a container, where localhost = the container, not the host. A
# host-published eon port must be reached via host.docker.internal, so auto-rewrite
# localhost/127.0.0.1 and warn — otherwise every request is "connection refused".
for v in TARGET_BASE_URL GUARDIAN_BASE_URL; do
  val="${!v:-}"
  if [[ "$val" =~ ^https?://(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    newval="${val/localhost/host.docker.internal}"; newval="${newval/127.0.0.1/host.docker.internal}"
    echo "▶ $v: rewriting $val → $newval (k6 runs in Docker; localhost would be the container)"
    export "$v=$newval"
  fi
done

# Headless unless there's a terminal and a way to open a browser.
if [[ -z "${HEADLESS:-}" ]]; then
  if [[ -t 1 ]] && { [[ "$(uname)" == "Darwin" ]] || [[ -n "${DISPLAY:-}" ]]; }; then HEADLESS=false; else HEADLESS=true; fi
fi
export HEADLESS

IMAGE="${K6_IMAGE_NAME:-eon-load-test-k6}"
echo "▶ Building $IMAGE (cached after the first time)…"
docker build -q -t "$IMAGE" . >/dev/null

# Sanitise COMBO the same way config.js does, so the folder we create matches the
# path the report is written to.
COMBO_RAW="${COMBO:-default}"
COMBO_DIR="$(printf '%s' "$COMBO_RAW" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]/-/g')"
echo "▶ Combo: $COMBO_DIR  (reports/$COMBO_DIR/)"

REPORTS_DIR="${REPORTS_DIR:-$PWD/reports}"
mkdir -p "$REPORTS_DIR/$COMBO_DIR"
DOCKER_ARGS=(--rm --ulimit nofile=65535:65535 -v "$REPORTS_DIR:/app/reports" --user "$(id -u):$(id -g)"
             --add-host=host.docker.internal:host-gateway)
[[ -n "${DOCKER_NETWORK:-}" ]] && DOCKER_ARGS+=(--network "$DOCKER_NETWORK")
for v in "${VARS[@]}"; do
  [[ -n "${!v:-}" ]] && DOCKER_ARGS+=(-e "$v=${!v}")
done

# Live page on a laptop: ui/live.html reads k6's stream on DASHBOARD_PORT.
LIVE_UI="${LIVE_UI:-true}"
DASHBOARD_PORT="${DASHBOARD_PORT:-5665}"
if [[ "$HEADLESS" == "false" && "$LIVE_UI" == "true" ]]; then
  DOCKER_ARGS+=(-p "${DASHBOARD_PORT}:${DASHBOARD_PORT}")
  if [[ -n "${REQUESTS_PER_SECOND:-}" ]]; then LOAD_RPS="$REQUESTS_PER_SECOND"
  else LOAD_RPS="$(awk "BEGIN{print ${REQUESTS_PER_MINUTE:-0}/60}")"; fi
  enc() { local s="$1" o="" c i; for ((i=0; i<${#s}; i++)); do c="${s:i:1}"; case "$c" in [a-zA-Z0-9.~_-]) o+="$c";; *) o+="$(printf '%%%02X' "'$c")";; esac; done; printf '%s' "$o"; }
  QS="mode=${TEST_MODE:-stress}&target=$(enc "${TARGET_BASE_URL:-}")&port=$DASHBOARD_PORT"
  QS+="&healthyErr=${HEALTHY_ERROR_RATE:-0.01}&healthyP95=${HEALTHY_P95_MS:-2000}&criticalErr=${CRITICAL_ERROR_RATE:-0.01}"
  QS+="&stepSeconds=${STRESS_STEP_SECONDS:-10}&startRequests=${STRESS_START_REQUESTS:-500}&stepRequests=${STRESS_STEP_REQUESTS:-500}&loadRps=$LOAD_RPS"
  LIVE_URL="file://$PWD/ui/live.html?$QS"
  echo "▶ Live view: $LIVE_URL"
  if [[ "${LIVE_UI_OPEN:-true}" == "true" ]] && command -v open >/dev/null 2>&1; then open "$LIVE_URL" 2>/dev/null || true; fi
fi

echo "▶ Running k6 in Docker (HEADLESS=$HEADLESS)…"
# Named container, always removed when this script ends (also on Ctrl+C / kill),
# so a stopped run never leaves a load generator running in the background.
CONTAINER="eon-loadtest-k6-$$"
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true' EXIT
trap 'echo; echo "▶ Stopping…"; docker stop -t 10 "$CONTAINER" >/dev/null 2>&1; exit 130' INT TERM
set +e
if [[ -t 0 && -t 1 ]]; then
  docker run --name "$CONTAINER" -it "${DOCKER_ARGS[@]}" "$IMAGE"   # interactive: Ctrl+C reaches the container
else
  docker run --name "$CONTAINER" "${DOCKER_ARGS[@]}" "$IMAGE" < /dev/null &
  wait $!                                                             # a kill of this script runs the clean-up
fi
CODE=$?
set -e
LATEST="$(ls -t "$REPORTS_DIR/$COMBO_DIR"/*.md 2>/dev/null | head -1 || true)"
[[ -n "$LATEST" ]] && echo "▶ Markdown report: $LATEST"
echo "▶ When all 4 combos are done: ./consolidate.sh"
exit "$CODE"
