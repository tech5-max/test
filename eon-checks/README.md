# eon-checks — verify the load-test switches work

Confirms the server-side switches added for load testing actually do what they
claim. Two are checked automatically against the real service source (no DB/Redis
needed); three are runtime checks you run against a live eon.

## Automatic checks (Bun, no DB/Redis)
```bash
./run.sh
```
Runs `cache-pool-check.ts` in two configs and asserts:
- **`CACHE_ENABLED=false`** → both services' `redisClient` is the inert stub
  (`get()` returns `null`, `set()` is a resolving no-op, `status === "disabled"`),
  so no response cache is read or written. **`CACHE_ENABLED=true`** → a real
  ioredis client.
- **`DB_POOL_MAX=25`** → every pool's `max` is 25 (timeSeries & portfolio keep
  their floor of 20, raised with it). Default (unset) → 10, floor 20.

These import `fast-dragon/services/redis.ts`, `express-guardian/utils/redis.ts` and
both `db/config.ts` directly; pg pools are lazy so nothing connects to a database.

## Runtime checks (need a running eon)
Set the env, (re)start the service, then:

**RATE_LIMIT_ENABLED** (express-guardian; fast-dragon is the same):
```bash
# limiter OFF: 600 fast requests from one IP, expect zero 429
RATE_LIMIT_ENABLED=false  # restart eon, then:
for i in $(seq 1 600); do curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:5001/api/user/profile; done | sort | uniq -c
# expect: no 429 lines. With RATE_LIMIT_ENABLED=true you get 429s past the limit.
```

**RUN_CRONS** — grep the boot log:
```bash
# RUN_CRONS=false → fast-dragon prints "RUN_CRONS=false — background warmers/crons skipped"
#                   express-guardian prints "RUN_CRONS=false — startup migrations and crons skipped"
# RUN_CRONS=true  → the warmer/migration log lines appear instead.
```

**Multi-container load balancer** (`docker-compose.loadtest.yml`):
```bash
cd ../../predictive-ai-web-services
docker compose -f docker-compose.loadtest.yml up --build --scale fast-dragon=3 -d
# the LB adds an X-Upstream header; hit it a few times and watch it rotate:
for i in 1 2 3 4 5 6; do curl -s -D - -o /dev/null http://localhost:8080/api/top-coins \
  | grep -i x-upstream; done
# expect several distinct upstream addresses = traffic spread across the 3 replicas.
```

These three depend on the live stack, so they live here as documented commands
rather than an automated harness.
