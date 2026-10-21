#!/usr/bin/env bash
# Runs the DDoS-resilience flood in Docker (the only requirement on the host).
# Run it from a SEPARATE machine against YOUR OWN server — never on the server
# itself (you'd block your own SSH).
#   ./run.sh                          # settings from .env
#   PHASE_LABEL=no-fail2ban ./run.sh  # phase 1
#   PHASE_LABEL=fail2ban   ./run.sh   # phase 2 (after enabling fail2ban)
#   ./run.sh --combine                # build the before/after comparison
set -euo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null 2>&1 || { echo "✗ Docker is required"; exit 1; }

ENV_FILE="${ENV_FILE:-.env}"
VARS=(TARGET_BASE_URL ENDPOINT REQUESTS_PER_SECOND DURATION PHASE_LABEL
      BAN_CONFIRM_SECONDS CONNECT_TIMEOUT MONITOR_EVERY MAX_INFLIGHT DEGRADE_P95_MS ALLOW_LOCAL)
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    key="${line%%=*}"; value="${line#*=}"; key="$(echo "$key" | xargs)"
    [[ -z "${!key:-}" ]] && export "$key=$value"
  done < "$ENV_FILE"
fi

IMAGE="${DDOS_IMAGE_NAME:-ozak-ddos-resilience}"
REPORTS_DIR="${REPORTS_DIR:-$PWD/reports}"
mkdir -p "$REPORTS_DIR"
echo "▶ Building ${IMAGE}…"
docker build -q -t "$IMAGE" . >/dev/null

# --combine: produce the before/after comparison from existing reports.
if [[ "${1:-}" == "--combine" ]]; then
  docker run --rm -v "$REPORTS_DIR:/app/reports" --user "$(id -u):$(id -g)" \
    --entrypoint python3 "$IMAGE" report.py --combine
  exit 0
fi

DOCKER_ARGS=(--rm --ulimit nofile=65535:65535 -v "$REPORTS_DIR:/app/reports" --user "$(id -u):$(id -g)"
             --add-host=host.docker.internal:host-gateway)
[[ -n "${DOCKER_NETWORK:-}" ]] && DOCKER_ARGS+=(--network "$DOCKER_NETWORK")
for v in "${VARS[@]}"; do [[ -n "${!v:-}" ]] && DOCKER_ARGS+=(-e "$v=${!v}"); done

CONTAINER="ozak-ddos-$$"
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true' EXIT
trap 'echo; echo "▶ Stopping…"; docker stop -t 8 "$CONTAINER" >/dev/null 2>&1; exit 130' INT TERM
echo "▶ Running flood in Docker (PHASE=${PHASE_LABEL:-flood})…"
set +e
if [[ -t 0 && -t 1 ]]; then
  docker run --name "$CONTAINER" -it "${DOCKER_ARGS[@]}" "$IMAGE"
else
  docker run --name "$CONTAINER" "${DOCKER_ARGS[@]}" "$IMAGE" < /dev/null &
  wait $!
fi
CODE=$?
set -e
LATEST="$(ls -t "$REPORTS_DIR"/*.md 2>/dev/null | head -1 || true)"
[[ -n "$LATEST" ]] && echo "▶ Markdown report: $LATEST"
exit "$CODE"
