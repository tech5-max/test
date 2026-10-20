# Incident simulator and verification

This is a fake API that fails **on purpose**, used to prove that both load-test tools (k6 and Locust) detect and report each kind of incident correctly. It's Python standard library only, and it runs in Docker.

## Verify both tools
```bash
cd incident-sim
./verify.sh                          # all scenarios × both tools, ~20 min
TOOLS=k6 ./verify.sh                 # one tool
SCENARIOS="5xx timeout" ./verify.sh  # some scenarios
```
Each scenario:
1. starts the simulator in Docker
2. runs the tool headless against it (small rates: 30 → 60 → 90 … req/s)
3. checks the JSON report and the terminal output against the expected result

At the end it prints a **PASS/FAIL table**. Logs and reports are in `out/`.

| Scenario | What the simulator does (above 60 req/s) | Expected result |
|---|---|---|
| `healthy-load` | always fast | load mode 🟢, achieved ≈ target |
| `healthy` | always fast | stress runs to the cap, no 🔴 |
| `slow` | latency grows with load, never fails | **no stop**, steps go 🟡, runs to the cap |
| `timeout` | stops answering | 🔴 at the right step, cause = timeouts |
| `5xx` | answers 500 | 🔴, cause = 5xx |
| `refused` | stops listening | 🔴, cause = connection refused |
| `reset` | resets connections | 🔴, cause = connection reset |
| `stray5xx` | exactly 2 errors in the whole run | **no stop** (below 1% of a step) |
| `ratelimit` | 429s with RateLimit headers | no 🔴; limiter warning shown |
| `generator` | healthy, but the tool gets tiny `MAX_VUS` | **no 🔴**; ⚠ generator-limited; 🟢 "not measured" |

Every run also checks:
- the 10-second status lines and the final result appear in the terminal
- the Markdown report is written
- requests the tool cancelled while stopping are not counted

## Run the simulator by hand
```bash
docker build -t ozak-incident-sim .
docker run --rm -p 8080:8080 -e SCENARIO=5xx -e FAIL_ABOVE_RPS=100 ozak-incident-sim
```
Settings:
- `SCENARIO`: `healthy`, `slow`, `timeout`, `5xx`, `refused`, `reset`, `stray5xx` or `ratelimit`
- `FAIL_ABOVE_RPS`: the load at which it starts failing
- `BASE_LATENCY_MS`: normal response time
- `STRAY_COUNT`: number of stray errors in the `stray5xx` scenario
