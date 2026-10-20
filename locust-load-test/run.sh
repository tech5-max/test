#!/usr/bin/env bash
# Runs the Locust load test in Docker (the only requirement on the host).
#   ./run.sh                     # settings from .env
#   ENV_FILE=.env.stage ./run.sh # another env file
#   TEST_MODE=load ./run.sh      # inline overrides win over the env file
# Terminal: one status line every 10 s, then the full result.
# Reports: reports/<mode>-<time>.md (+ .json)
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || { echo "✗ Docker is required (https://docs.docker.com/engine/install/)"; exit 1; }

ENV_FILE="${ENV_FILE:-.env}"
VARS=(TARGET_BASE_URL TEST_MODE INCLUDE_AI_ANALYSIS
      REQUESTS_PER_MINUTE REQUESTS_PER_SECOND DURATION
      STRESS_START_REQUESTS STRESS_STEP_REQUESTS STRESS_STEP_SECONDS STRESS_MAX_DURATION
      HEALTHY_ERROR_RATE HEALTHY_P95_MS CRITICAL_ERROR_RATE
      MAX_VUS REQUEST_TIMEOUT AI_REQUEST_TIMEOUT MONITOR_EVERY LOCUST_PROCESSES LOCUST_LOGLEVEL)

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

IMAGE="${LOCUST_IMAGE_NAME:-ozak-load-test-locust}"
echo "▶ Building $IMAGE (cached after the first time)…"
docker build -q -t "$IMAGE" . >/dev/null

REPORTS_DIR="${REPORTS_DIR:-$PWD/reports}"
mkdir -p "$REPORTS_DIR"
DOCKER_ARGS=(--rm --ulimit nofile=65535:65535 -v "$REPORTS_DIR:/app/reports" --user "$(id -u):$(id -g)"
             --add-host=host.docker.internal:host-gateway -e HOME=/tmp)
[[ -n "${DOCKER_NETWORK:-}" ]] && DOCKER_ARGS+=(--network "$DOCKER_NETWORK")
for v in "${VARS[@]}"; do
  [[ -n "${!v:-}" ]] && DOCKER_ARGS+=(-e "$v=${!v}")
done

echo "▶ Running Locust in Docker…"
# Named container, always removed when this script ends (also on Ctrl+C / kill),
# so a stopped run never leaves a load generator running in the background.
CONTAINER="ozak-loadtest-locust-$$"
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
LATEST="$(ls -t "$REPORTS_DIR"/*.md 2>/dev/null | head -1 || true)"
[[ -n "$LATEST" ]] && echo "▶ Markdown report: $LATEST"
exit "$CODE"
