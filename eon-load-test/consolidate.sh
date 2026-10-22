#!/usr/bin/env bash
# Merge the 4 combo reports into one. Runs in the same Docker image as the test
# (python:3.12-alpine base), so the host needs nothing but Docker.
#
#   ./consolidate.sh                       # auto-discover reports/<combo>/
#   ./consolidate.sh reports/a/x.json ...  # explicit combo json files
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || { echo "✗ Docker is required"; exit 1; }

IMAGE="${K6_IMAGE_NAME:-eon-load-test-k6}"
# Build if the image isn't there yet (first use before any run.sh).
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -q -t "$IMAGE" . >/dev/null

REPORTS_DIR="${REPORTS_DIR:-$PWD/reports}"
[[ -d "$REPORTS_DIR" ]] || { echo "✗ No reports/ yet — run ./run.sh for each combo first."; exit 1; }

docker run --rm -v "$REPORTS_DIR:/app/reports" --user "$(id -u):$(id -g)" \
  --entrypoint python3 "$IMAGE" /app/consolidate.py "$@"
