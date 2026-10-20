#!/bin/sh
# Runs inside the container. Settings arrive as environment variables (k6 reads
# them as __ENV). HEADLESS=true (servers/CI): k6 runs quietly and monitor.py
# prints a status line every 10 s. Otherwise k6 shows its progress bar.
set -u
cd /app

ulimit -n 65535 2>/dev/null || ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
echo "▶ Open-file limit in the container: $(ulimit -n)"

# k6's live metrics stream: read by monitor.py (headless) and ui/live.html (laptop).
export K6_WEB_DASHBOARD=true
export K6_WEB_DASHBOARD_HOST=0.0.0.0
export K6_WEB_DASHBOARD_PORT="${DASHBOARD_PORT:-5665}"
export K6_WEB_DASHBOARD_PERIOD=2s

if [ "${HEADLESS:-false}" = "true" ]; then
  python3 /app/monitor.py &
  MON=$!
  k6 run --quiet src/script.js
  CODE=$?
  sleep 2
  kill "$MON" 2>/dev/null
else
  k6 run src/script.js
  CODE=$?
fi

# 99 = thresholds crossed, 108 = stopped by a threshold (stress mode found the
# crash point). Both are normal test outcomes, not tool failures.
case "$CODE" in
  0) ;;
  99|108) echo "▶ k6 exit code $CODE: limits crossed (expected when the server is pushed past them). See the report." ;;
  *) echo "▶ k6 exit code $CODE" ;;
esac
exit "$CODE"
