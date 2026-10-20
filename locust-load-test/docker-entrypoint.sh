#!/bin/sh
# Runs inside the container. Settings arrive as environment variables.
set -u
cd /app
ulimit -n 65535 2>/dev/null || ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
echo "▶ Open-file limit in the container: $(ulimit -n)"

PROCS="${LOCUST_PROCESSES:--1}"
echo "▶ Locust worker processes: ${PROCS} (-1 = one per CPU core: $(nproc))"

# --headless: no web UI; --only-summary: no periodic stats tables (our own
# status line is printed instead); the shape class in locustfile.py drives
# users and decides when to stop.
exec locust -f locustfile.py --headless --only-summary --processes "$PROCS" \
  --host "${TARGET_BASE_URL:-http://localhost}" --loglevel "${LOCUST_LOGLEVEL:-WARNING}" --exit-code-on-error 0
