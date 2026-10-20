#!/usr/bin/env bash
# Verifies both load-test services (k6 and Locust) against simulated incidents.
# Needs only Docker. Each scenario runs the tool headless against a local fake
# API that fails in a specific way, then checks the JSON report and terminal.
#   ./verify.sh                 # all scenarios, both tools
#   TOOLS=k6 ./verify.sh        # one tool
#   SCENARIOS="5xx timeout" ./verify.sh
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
OUT="${OUT:-$PWD/out}"
NET=loadtest-verify
TOOLS="${TOOLS:-k6 locust}"
SCENARIOS="${SCENARIOS:-healthy-load fractional-load healthy slow timeout 5xx refused reset stray5xx ratelimit generator}"

command -v docker >/dev/null || { echo "✗ Docker is required"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
echo "▶ Building images…"
docker build -q -t ozak-incident-sim . >/dev/null
docker build -q -t ozak-verify-check -f - . >/dev/null <<'EOF'
FROM python:3.12-alpine
COPY check.py /check.py
ENTRYPOINT ["python3", "/check.py"]
EOF

declare -a ROWS
fails=0
for sc in $SCENARIOS; do
  for tool in $TOOLS; do
    # Scenario → simulator behaviour + test settings (small, fast rates).
    sim_scenario="$sc"; base=20; extra=()
    case "$sc" in
      healthy-load) sim_scenario=healthy; extra=(TEST_MODE=load REQUESTS_PER_SECOND=50 DURATION=25s) ;;
      fractional-load) sim_scenario=healthy; extra=(TEST_MODE=load REQUESTS_PER_MINUTE=1000 DURATION=30s) ;;
      healthy)      extra=(STRESS_MAX_DURATION=40s) ;;
      slow)         extra=(STRESS_MAX_DURATION=50s) ;;
      stray5xx)     extra=(STRESS_MAX_DURATION=40s) ;;
      ratelimit)    extra=(STRESS_MAX_DURATION=30s) ;;
      generator)    sim_scenario=healthy; base=200; extra=(STRESS_MAX_DURATION=30s MAX_VUS=5 PRE_ALLOCATED_VUS=5) ;;
      *)            extra=(STRESS_MAX_DURATION=1m) ;;
    esac

    # No leftover load generators from an earlier (interrupted) run.
    docker ps -q --filter "name=ozak-loadtest-" | xargs -r docker rm -f >/dev/null 2>&1
    docker rm -f incident-sim >/dev/null 2>&1
    docker run -d --name incident-sim --network "$NET" -e SCENARIO="$sim_scenario" -e FAIL_ABOVE_RPS=60 -e BASE_LATENCY_MS="$base" ozak-incident-sim >/dev/null
    sleep 1
    dir="$OUT/$tool-$sc"; mkdir -p "$dir"
    svc="$ROOT/load-test"; [[ "$tool" == "locust" ]] && svc="$ROOT/locust-load-test"
    printf "▶ %-7s %-16s … " "$tool" "$sc"
    start=$(date +%s)
    env ENV_FILE=/dev/null REPORTS_DIR="$dir" HEADLESS=true LIVE_UI=false DOCKER_NETWORK="$NET" \
      TARGET_BASE_URL=http://incident-sim:8080 TEST_MODE=stress \
      STRESS_START_REQUESTS=300 STRESS_STEP_REQUESTS=300 STRESS_STEP_SECONDS=10 REQUEST_TIMEOUT=5s \
      "${extra[@]}" "$svc/run.sh" < /dev/null > "$dir/terminal.log" 2>&1
    verdict=$(docker run --rm -v "$dir:/run" ozak-verify-check "$sc" "$tool" /run /run/terminal.log 2>&1)
    took=$(( $(date +%s) - start ))
    echo "$verdict (${took}s)"
    [[ "$verdict" == PASS* ]] || fails=$((fails + 1))
    ROWS+=("$(printf "%-8s %-16s %s" "$tool" "$sc" "$verdict")")
  done
done
docker rm -f incident-sim >/dev/null 2>&1

echo; echo "════ Verification results ════"
printf "%-8s %-16s %s\n" "TOOL" "SCENARIO" "RESULT"
for r in "${ROWS[@]}"; do echo "$r"; done
echo; [[ $fails -eq 0 ]] && echo "✅ All ${#ROWS[@]} checks passed. Logs and reports: $OUT" || echo "❌ $fails of ${#ROWS[@]} checks failed. Logs and reports: $OUT"
exit $(( fails > 0 ))
