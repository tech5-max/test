# Ozak public API load test: k6

This service load-tests fast-dragon's **public GET APIs** as a logged-out visitor, optionally including the AI analysis APIs. It sends GET requests only, without cookies, and changes no data. **It runs entirely in Docker**: the image holds k6 2.3.0 plus Python for the terminal monitor.

## Install and start
Only Docker is needed (see [../README.md](../README.md) for installing it on a VPS).
```bash
cd load-test
cp .env.example .env      # set TARGET_BASE_URL and TEST_MODE (see below)
./run.sh                  # builds the image the first time, then runs
```
Any setting can be overridden for one run:
```bash
TEST_MODE=load REQUESTS_PER_MINUTE=3000 DURATION=5m ./run.sh
TEST_MODE=stress STRESS_STEP_REQUESTS=1000 ./run.sh
ENV_FILE=.env.stage ./run.sh
```

**Before a capacity test,** turn the server's rate limiter off: set `RATE_LIMIT_ENABLED=false` in fast-dragon, restart, and turn it back on afterwards. fast-dragon allows 60 requests/min per IP for logged-out visitors, and all test traffic comes from one IP. The test warns at startup if the limiter is on.

## Modes
- **`load`:** sends exactly `REQUESTS_PER_MINUTE` (or `REQUESTS_PER_SECOND`) for `DURATION`. The report gives one verdict for that rate, with its metrics.
- **`stress`:**
  - Step 1 sends `STRESS_START_REQUESTS` within `STRESS_STEP_SECONDS`. Every next step adds `STRESS_STEP_REQUESTS` more: 500, 1000, 1500 … per 10 s, which is 50, 100, 150 … req/s. Parallel users grow automatically.
  - **It stops only when the server fails:** 5xx or no response from the server on more than `CRITICAL_ERROR_RATE` of a step's requests, judged on each whole step.
  - Slow responses never stop it. `STRESS_MAX_DURATION` is a safety cap.

## What counts as a failure
Every request lands in exactly one class:

| Class | Meaning | Server crash? |
|---|---|---|
| `server_5xx` | the server answered 5xx | ✅ |
| `timeout` | no answer within `REQUEST_TIMEOUT` | ✅ |
| `refused` | connection refused | ✅ |
| `reset` | connection reset or dropped | ✅ |
| `unreachable` | couldn't reach the server | ✅ |
| `tls` | TLS handshake failed | ✅ |
| `other_network` | any other network error | ✅ |
| `client_limit` | the **test machine** ran out of sockets/files, or DNS failed | ❌ shown as ⚠ generator limit |
| `rate_limited` | 429 | ❌ warning |
| `other_4xx` | other 4xx | ❌ |

Requests k6 cancels itself when the test stops are not counted at all.

## Stages (per step)

| Stage | Rule |
|---|---|
| 🟢 **Easily serves** | errors ≤ `HEALTHY_ERROR_RATE` and p95 ≤ `HEALTHY_P95_MS` |
| 🟡 **Under stress but survives** | in between: slow, or a few non-crash errors |
| 🔴 **Critical: server crash** | crash-class failures > `CRITICAL_ERROR_RATE` |

A step is **⚠ generator-limited** when the test machine itself failed (`client_limit` errors), or when k6 hit `MAX_VUS` and couldn't send the planned load. Those steps don't count toward 🟢 / 🟡, because they describe the test machine, not the server.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TARGET_BASE_URL` | required | Everything before `/api`, e.g. `https://api.ozak.dev` |
| `TEST_MODE` | `stress` | `load` or `stress` |
| `INCLUDE_AI_ANALYSIS` | `false` | `true` also tests the 4 AI APIs |
| `REQUESTS_PER_MINUTE` / `REQUESTS_PER_SECOND` | — | `load` rate |
| `DURATION` | `2m` | `load` length |
| `STRESS_START_REQUESTS` / `STRESS_STEP_REQUESTS` / `STRESS_STEP_SECONDS` | `500` / `500` / `10` | `stress` schedule |
| `STRESS_MAX_DURATION` | `30m` | `stress` safety cap |
| `HEALTHY_ERROR_RATE` / `HEALTHY_P95_MS` | `0.01` / `2000` | 🟢 limits |
| `CRITICAL_ERROR_RATE` | `0.01` | 🔴 limit (stress stops here). `0` = the very first failure |
| `PRE_ALLOCATED_VUS` / `MAX_VUS` | `500` / `5000` | k6 users available |
| `REQUEST_TIMEOUT` / `AI_REQUEST_TIMEOUT` | `15s` / `60s` | Longer counts as a timeout |
| `HEADLESS` | auto | `true` on servers: status lines, no browser. Detected automatically when there's no terminal or display |
| `MONITOR_EVERY` | `10` | Seconds between status lines |
| `LOG_ERRORS` | `true` | Print a few sample failing requests, with the real error message |
| `LIVE_UI` / `DASHBOARD_PORT` | `true` / `5665` | Laptop only: the live browser page |
| `DOCKER_NETWORK` / `REPORTS_DIR` | — | Advanced: Docker network to join, and where to write reports |

## Output
- **Terminal on a server (`HEADLESS`):**
  ```
  [00:50] step 5 | target 250/s | done 247/s | users 310 | p95 1.8s p99 2.6s | ok 99.6% | failures: timeout 9 | 🟡 Under stress
  ```
  At the end comes the full result: the three stages, maximum load survived, peak throughput, why it stopped, the step table and failures by type.
- **On a laptop:** k6's progress bar, plus `ui/live.html`, which opens automatically with live req/s, users, p95/p99, errors and stage.
- **Reports** in `reports/`:
  - `<mode>[-ai]-<time>.md`: the report
  - `.html`: the same with a chart
  - `.json`: all data

Response times are measured from the machine running k6, so they include network time. Run it from a machine close to the server for server-only numbers. A stress run pushes the target until it fails, so don't run one while others depend on stage.
