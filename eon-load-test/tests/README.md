# eon-load-test self-test

Checks the 4-combo harness end to end without touching eon, using the
[incident simulator](../../incident-sim/README.md) as a fake eon that answers
every path. Docker only.

```bash
./verify.sh
```

What it does:
1. Starts one healthy fake eon on a Docker network.
2. Runs the harness 4 times — `single-cache-on`, `single-cache-off`,
   `multi-cache-on`, `multi-cache-off` — in `load` mode, with a dummy cookie so the
   express-guardian endpoints are included too, all into one `out/reports/`.
3. Runs `consolidate.sh` over those reports.
4. Asserts (via `check.py`, in Docker): each combo wrote a JSON with a non-empty
   per-endpoint table (every endpoint has p95) and `summary.requests > 0`, and the
   consolidated report names all 4 combos and has a row for every endpoint.

Prints `PASS`/`FAIL`. The simulator only validates the **harness** (labelling,
per-endpoint tables, consolidation) — it does not exercise the eon cache or
rate-limit switches; those are checked separately in
[`../../eon-checks`](../../eon-checks/README.md).

## realtime-test.sh — the real eon stack, locally

```bash
./realtime-test.sh                       # full matrix
COMBOS="single-cache-on" ./realtime-test.sh   # one combo
```

Boots both services in containers against the real stage/prod DBs via
`docker-compose.realtime.yml`, with `RUN_CRONS=false` (no migrations/crons), an
isolated redis for the cache, cookie set, and AI/portfolio off. Runs exactly the 4
conditions (rate limiting off in all — cache-off disables it via `CACHE_ENABLED=false`,
cache-on via `RATE_LIMIT_ENABLED=false`):

| Combo | replicas | cache | rate-limit |
|---|---|---|---|
| single-cache-on | 1 | on | off |
| single-cache-off | 1 | off | off (coupled) |
| multi-cache-on | 3 | on | off |
| multi-cache-off | 3 | off | off (coupled) |

Cache-on runs are warmed first (one read-only pass) so the cache isn't cold. The
harness runs on the compose network and targets the nginx LB (`lb:8080`/`:8081`).
After the runs it consolidates and runs `scan.py`, which separates real harness/switch
bugs from server/env config issues (e.g. an unset warehouse) and from capacity
timeouts. Output + logs land in `tests/out-realtime/`.
