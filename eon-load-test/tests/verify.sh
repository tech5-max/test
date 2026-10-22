#!/usr/bin/env bash
# Verifies the eon 4-combo harness end to end against the incident simulator
# (a fake eon that answers every path). Docker only. Runs 4 labelled combos in
# `load` mode, consolidates them, and asserts the reports are correct.
#   ./verify.sh
set -uo pipefail
cd "$(dirname "$0")"
HARNESS="$(cd .. && pwd)"
SIM="$(cd ../../incident-sim && pwd)"
OUT="${OUT:-$PWD/out}"
NET=eon-loadtest-verify
COMBOS=(single-cache-on single-cache-off multi-cache-on multi-cache-off)

command -v docker >/dev/null || { echo "✗ Docker is required"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT/reports"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

echo "▶ Building images…"
docker build -q -t ozak-incident-sim "$SIM" >/dev/null
docker build -q -t eon-load-test-k6 "$HARNESS" >/dev/null

# One healthy fake eon for all combos (the harness only labels; it doesn't change
# the server). A dummy cookie turns on the express-guardian endpoints too.
docker rm -f eon-sim >/dev/null 2>&1
docker run -d --name eon-sim --network "$NET" -e SCENARIO=healthy -e FAIL_ABOVE_RPS=100000 ozak-incident-sim >/dev/null
sleep 1
trap 'docker rm -f eon-sim >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1 || true' EXIT

for combo in "${COMBOS[@]}"; do
  printf "▶ combo %-16s … " "$combo"
  env ENV_FILE=/dev/null REPORTS_DIR="$OUT/reports" HEADLESS=true LIVE_UI=false \
    DOCKER_NETWORK="$NET" COMBO="$combo" \
    TARGET_BASE_URL=http://eon-sim:8080 GUARDIAN_BASE_URL=http://eon-sim:8080 \
    COOKIE="siwe-token=test" TEST_MODE=load REQUESTS_PER_SECOND=60 DURATION=12s \
    REQUEST_TIMEOUT=5s "$HARNESS/run.sh" < /dev/null > "$OUT/$combo.log" 2>&1
  if ls "$OUT/reports/$combo"/*.json >/dev/null 2>&1; then echo "ran"; else echo "NO REPORT (see $OUT/$combo.log)"; fi
done

echo "▶ consolidating…"
REPORTS_DIR="$OUT/reports" "$HARNESS/consolidate.sh" > "$OUT/consolidate.log" 2>&1 || true

echo "▶ checking reports…"
docker build -q -t eon-verify-check -f - "$PWD" >/dev/null <<'EOF'
FROM python:3.12-alpine
COPY check.py /check.py
ENTRYPOINT ["python3", "/check.py"]
EOF
IFS=,; combolist="${COMBOS[*]}"; unset IFS
verdict=$(docker run --rm -v "$OUT/reports:/run" eon-verify-check /run "$combolist" 2>&1)
echo "$verdict"

echo; if [[ "$verdict" == *PASS* ]]; then
  echo "✅ Harness verified. Reports + logs: $OUT"
  exit 0
else
  echo "❌ Harness check failed. Reports + logs: $OUT"
  exit 1
fi
