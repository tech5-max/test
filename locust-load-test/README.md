# Ozak public API load test: Locust

This is the same test as the [k6 service](../load-test/README.md), built on **Locust 2.46.7**. It has the same modes, settings, failure classes, stages and report layout. **It runs entirely in Docker**, using one Locust worker per CPU core.

## Install and start
Only Docker is needed (see [../README.md](../README.md) for installing it on a VPS).
```bash
cd locust-load-test
cp .env.example .env      # set TARGET_BASE_URL and TEST_MODE
./run.sh                  # builds the image the first time, then runs
```
Override any setting for one run:
```bash
TEST_MODE=load REQUESTS_PER_SECOND=200 DURATION=5m ./run.sh
TEST_MODE=stress STRESS_STEP_REQUESTS=1000 LOCUST_PROCESSES=8 ./run.sh
```

**Before a capacity test,** turn fast-dragon's rate limiter off (`RATE_LIMIT_ENABLED=false`), and turn it back on afterwards. The test warns at startup if it's still on.

## How the load is made
- **Users = target req/s rounded up**; each user paces itself at target ÷ users (at most 1 req/s), so fractional rates such as 40 req/min are sent exactly.
  - **`load`:** `REQUESTS_PER_SECOND` (or `REQUESTS_PER_MINUTE` ÷ 60) users for `DURATION`.
  - **`stress`:** users rise every `STRESS_STEP_SECONDS`, starting at `STRESS_START_REQUESTS ÷ STRESS_STEP_SECONDS` req/s and adding `STRESS_STEP_REQUESTS ÷ STRESS_STEP_SECONDS` each step (500/10 s = +50 req/s).
- **Stop rule:** the run stops at the first finished step where the server failed (5xx or no response) on more than `CRITICAL_ERROR_RATE` of its requests. Slow responses never stop it.
- **When the server slows down,** each user takes longer per request, so **Completed** falls below **Sent**. With no failures, that means the server is queueing requests: you've found its throughput limit.
- **`LOCUST_PROCESSES=-1`** (default) runs one worker per CPU core. Workers send their numbers to the master every 3 s; the master prints the status line and writes the report.

## Failure classes, stages and report
These are identical to k6; see the tables in [../load-test/README.md](../load-test/README.md#what-counts-as-a-failure). In short:
- 🔴 = 5xx / timeout / refused / reset / unreachable / TLS on more than 1% of a step.
- Test-machine errors (out of sockets or files, DNS) and users capped by `MAX_VUS` or CPU overload are shown as **⚠ generator-limited**, not counted against the server.
- Requests in flight when the test stops are not counted.

## Settings (`.env`)
The same as k6 (`TARGET_BASE_URL`, `TEST_MODE`, `INCLUDE_AI_ANALYSIS`, `REQUESTS_PER_MINUTE/SECOND`, `DURATION`, `STRESS_*`, `HEALTHY_*`, `CRITICAL_ERROR_RATE`, `REQUEST_TIMEOUT`, `AI_REQUEST_TIMEOUT`, `MONITOR_EVERY`), plus:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_VUS` | `5000` | Max parallel Locust users (normally users = req/s; if capped, each user paces faster) |
| `LOCUST_PROCESSES` | `-1` | Worker processes (`-1` = one per CPU core) |
| `LOCUST_LOGLEVEL` | `WARNING` | Locust's own log level |

## Output
- **Terminal:** a status line every 10 s, then the full result:
  ```
  [00:21] step 3 | target 90/s | done 87/s | users 90 | p95 1.3s p99 1.6s | ok 100.0% | failures: none | 🟢 Easily serves
  ```
- **Reports** in `reports/`: `<mode>[-ai]-<time>.md` (the report) and `.json` (all data).
