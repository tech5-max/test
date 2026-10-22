# eon full load test (k6) — 4 combos + consolidation

Load/stress test for the **full eon backend** (fast-dragon + express-guardian), run
once per **combo** (cache on/off × single/multi process), each report saved
separately, then merged into one consolidated report. Same k6 engine, modes and
report style as [`../load-test`](../load-test/README.md); this one adds the second
service, cookie auth, combo labels and consolidation. Runs entirely in **Docker**.

## Model: you drive eon, the harness only sends traffic
The harness never starts or stops eon. **You** restart eon into each state, then run
the harness with a label that says which state it was — the label just picks the
report folder (`reports/<combo>/`). So the flow per combo is: set eon's env →
restart eon → run the harness with the matching `COMBO`.

## The server switches (set in each service's `.env`, then restart)
Implemented in `predictive-ai-web-services` (`fast-dragon`, `express-guardian`):

| Env | Effect |
|---|---|
| `CACHE_ENABLED=false` | Swaps the Redis **response-cache** client for an inert stub (reads miss, writes no-op → handlers recompute) **and disables rate limiting** in both services. This is the whole "cache-off" state: one switch turns off both the cache and the limiter. Default `true`. |
| `RATE_LIMIT_ENABLED=false` | Independently turns the limiter off while the cache stays on (used by the cache-**on** combos, since rate limiting is always off during load testing). Both services. Default `true`. |
| `RUN_CRONS=false` | Skips boot warmers/crons/migrations. Run **one** instance `true`, the rest `false`. Default `true`. |
| `DB_POOL_MAX=30` | Per-pool max DB connections (default 10). Raise for stage load. |

**The cache switch couples both:** `cache-off` = cache **and** rate limiting off;
`cache-on` = cache on (rate limiting is kept off during the test via
`RATE_LIMIT_ENABLED=false`). So the 4 combos differ only by **process count × cache**.

> **Cache-off caveat:** the AI/paid-feed routes depend on the cache (they'd hit
> Moralis/OpenAI/Kaiko/CoinGlass per request, or 503). The harness enforces this:
> cache-off runs set `CACHE_OFF=true`, which **force-excludes the AI + portfolio
> group**. Cache-off measures the cheap read/compute path only.

> **Prod DB safety:** only `userDetailsDB` has a stage copy; everything else is prod.
> These endpoints are all **reads**. Keep the rate sane on cache-off (every request
> recomputes against prod DBs).

## Get a session cookie (once)
Log into eon in a browser (dummy `astra@ozak.dev`, OTP `987678`), open DevTools →
Application → Cookies, copy the `siwe-token=...` cookie into `COOKIE`. Sessions live
in Postgres and are shared across both services, so the same cookie unlocks gated
fast-dragon routes and all express-guardian reads.

## Run the 4 combos

There's **no bundled load balancer** — you already run nginx on the server. Single
process = one container (harness hits it directly); multi process = N containers on
distinct ports with **your nginx** upstreaming across them (harness hits your nginx).
Each combo is two steps: **(1)** boot eon in that state, **(2)** run the harness with
the matching `COMBO` label. Prep once:
```bash
cp .env.example .env          # set GUARDIAN_BASE_URL, COOKIE, rates
```

> **Reaching eon from the harness:** k6 runs **inside a container**, so `localhost`
> there is the container, not your host. Point it at **`host.docker.internal`** for
> a host-published port (`run.sh` auto-rewrites `localhost`/`127.0.0.1` and warns, so
> either works). On the server, use the host/IP where your nginx listens.

**1 — single-cache-on**  (1 container · cache on · rate-limit off)
```bash
cd ../../predictive-ai-web-services
CACHE_ENABLED=true RATE_LIMIT_ENABLED=false RUN_CRONS=true \
  docker compose -f docker-compose.loadtest.yml up -d --build
cd ../test/eon-load-test
COMBO=single-cache-on \
  TARGET_BASE_URL=http://host.docker.internal:5555 GUARDIAN_BASE_URL=http://host.docker.internal:5001 ./run.sh
```

**2 — single-cache-off**  (1 container · cache off → **rate-limit off too**)
```bash
cd ../../predictive-ai-web-services
CACHE_ENABLED=false RUN_CRONS=true \
  docker compose -f docker-compose.loadtest.yml up -d --build
cd ../test/eon-load-test
COMBO=single-cache-off CACHE_OFF=true \
  TARGET_BASE_URL=http://host.docker.internal:5555 GUARDIAN_BASE_URL=http://host.docker.internal:5001 ./run.sh
```

**3 — multi-cache-on**  (N containers · cache on · rate-limit off)
```bash
cd ../../predictive-ai-web-services
# one cron owner + extra instances on other ports (see DEPLOYMENT.md §4):
CACHE_ENABLED=true RATE_LIMIT_ENABLED=false RUN_CRONS=true  FD_PORT=5555 EG_PORT=5001 docker compose -p eon1 -f docker-compose.loadtest.yml up -d --build
CACHE_ENABLED=true RATE_LIMIT_ENABLED=false RUN_CRONS=false FD_PORT=5556 EG_PORT=5002 docker compose -p eon2 -f docker-compose.loadtest.yml up -d
CACHE_ENABLED=true RATE_LIMIT_ENABLED=false RUN_CRONS=false FD_PORT=5557 EG_PORT=5003 docker compose -p eon3 -f docker-compose.loadtest.yml up -d
# add 5555-5557 / 5001-5003 to your nginx upstream, then point the harness at your nginx
# (host.docker.internal from the k6 container; on the server use the nginx host/IP):
cd ../test/eon-load-test
COMBO=multi-cache-on \
  TARGET_BASE_URL=http://host.docker.internal:<your-nginx-fd-port> GUARDIAN_BASE_URL=http://host.docker.internal:<your-nginx-eg-port> ./run.sh
```

**4 — multi-cache-off**  (N containers · cache off → **rate-limit off too**) — same as
#3 with `CACHE_ENABLED=false` on every instance and `COMBO=multi-cache-off CACHE_OFF=true`.

- Setting **`CACHE_ENABLED=false` alone** turns off both the cache and the limiter —
  that's the whole cache-off state. The cache-on combos pass `RATE_LIMIT_ENABLED=false`
  because rate limiting is always off while load testing.
- `CACHE_OFF=true` on the harness for the cache-off runs force-excludes AI + portfolio.
- Use the **exact** 4 labels above — `consolidate.sh` builds the report from only these.
- Exactly **one** instance per service runs `RUN_CRONS=true` so migrations/warmers
  happen once.

**Prefer it fully automated locally?** `tests/realtime-test.sh` runs all 4 combos
(boot → warm → load → down) and consolidates in one command, using a throwaway nginx
container just for the local multi-replica test — see below.

## Consolidate
```bash
./consolidate.sh         # merges the 4 canonical combos into one report
```
Writes `reports/consolidated-<time>.md` from **only** the 4 conditions
(`single-cache-on`, `single-cache-off`, `multi-cache-on`, `multi-cache-off`):
- **Combos** table — one row per condition (requests, success, peak throughput, p95/p99, failures, result).
- **Per-endpoint across combos** — every endpoint, one column per condition, each cell `p95 / error%`. Shows at a glance which APIs the cache and extra processes actually help.

## Settings
Same as `../load-test` (`TEST_MODE` load/stress, `REQUESTS_PER_*`, `DURATION`,
`STRESS_*`, `HEALTHY_*`, `CRITICAL_ERROR_RATE`, `MAX_VUS`, timeouts) plus
`GUARDIAN_BASE_URL`, `COOKIE`, `COMBO`, and **`CACHE_OFF`** (set `true` on cache-off
runs — it force-excludes the AI + portfolio group so those paid/uncached routes are
never hit without a cache, even if `INCLUDE_AI_ANALYSIS=true`). See `.env.example`.

> **Perps/derivatives need the warehouse:** `screener-perps`, `discover-perp-setups`
> and the derivatives routes read a **ClickHouse warehouse**. If
> `WAREHOUSE_CLICKHOUSE_URL` / `_USER` / `_PASSWORD` / `_DB` aren't set in
> fast-dragon's `.env`, those endpoints return 500 regardless of cache. Set them (see
> `fast-dragon/.env.example`) before trusting those rows.

## Check the harness itself
```bash
cd tests && ./verify.sh          # harness only: 4 combos against the incident simulator
cd tests && ./realtime-test.sh   # the REAL eon stack in local containers, full matrix
```
`realtime-test.sh` boots both services in containers (RUN_CRONS off, isolated redis,
AI off) and runs single/multi × cache on/off + rate-limit on/off, then a bug scan.
See [tests/README.md](tests/README.md).
