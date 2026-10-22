# Ozak load tests

There are two load-test services for the public (logged-out) GET APIs of fast-dragon, plus a simulator to check that they work. Everything runs in **Docker**; the server needs nothing else installed.

| Folder | Tool | What it is |
|---|---|---|
| [`load-test/`](load-test/README.md) | **k6** | Load and stress tests of the fast-dragon public GET APIs: terminal status lines, Markdown/HTML/JSON report, and a live browser page on a laptop |
| [`eon-load-test/`](eon-load-test/README.md) | **k6** | Full eon load test (fast-dragon + express-guardian), run once per combo (cache on/off × single/multi process) with cookie auth, a full per-endpoint table, and a consolidate command that merges the 4 reports into one |
| [`locust-load-test/`](locust-load-test/README.md) | **Locust** | The same two modes, the same settings and the same report layout, using all CPU cores |
| [`incident-sim/`](incident-sim/README.md) | — | A fake API that fails on purpose (slow, timeouts, 5xx, refused, reset…), plus `verify.sh`, which checks the load-test tools against it |
| [`eon-checks/`](eon-checks/README.md) | — | Verifies the eon load-test server switches (`CACHE_ENABLED` cache stub, `DB_POOL_MAX`, and runtime checks for `RATE_LIMIT_ENABLED` / `RUN_CRONS` / the multi-container load balancer) |
| [`ddos-resilience/`](ddos-resilience/README.md) | — | HTTP flood from one machine against **your own** server, to test fail2ban: a before/after defense report |
| [`fail2ban-guide/`](fail2ban-guide/FAIL2BAN-SETUP.md) | — | Step-by-step fail2ban + nginx rate-limit setup for the server |

Both tools have two modes:
- **`load`:** sends exactly the rate you set (`REQUESTS_PER_MINUTE` or `REQUESTS_PER_SECOND`) for `DURATION`, then reports how the server handled it.
- **`stress`:** raises the load every step until the **server fails**: 5xx or no response (timeout, refused, reset) on more than `CRITICAL_ERROR_RATE` (1%) of a step's requests. Slow responses never stop it.

The report gives three stages:
- 🟢 **Easily serves**
- 🟡 **Under stress but survives**
- 🔴 **Critical: server crash**

It also gives the peak throughput and the per-step numbers (sent vs completed, users, p85/p90/p95/p99/p99.9).

Errors that happen on the **test machine** (out of sockets or files, DNS, not enough users) are kept out of the server's numbers and flagged **⚠ generator-limited**. Requests the tool cancels itself when it stops are never counted.

---

## Run on a VPS (Docker only)

### 1. Install Docker (once)
Ubuntu/Debian:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER     # then log out and back in
docker run --rm hello-world       # check
```

### 2. Get the code
```bash
git clone <this repo> ozak-tests && cd ozak-tests
```

### 3. Run k6
```bash
cd load-test
cp .env.example .env              # set TARGET_BASE_URL, TEST_MODE, rates
./run.sh                          # first run builds the image (~1 min)
```

### 4. Run Locust
```bash
cd locust-load-test
cp .env.example .env              # same settings as k6
./run.sh
```

Each run prints:
- a **status line every 10 seconds**: step, target vs completed req/s, users, p95/p99, failures by type, stage
- then the **full result**

The Markdown report is written to `reports/` in that service's folder. On a VPS there's no browser, so the terminal output plus `reports/*.md` is everything you need.

**Before a capacity test:** turn the server's rate limiter off (`RATE_LIMIT_ENABLED=false` in fast-dragon's `.env`, then restart), and turn it back on afterwards. Both tools warn at startup if it's still on.

---

## Check the tools themselves (optional)
```bash
cd incident-sim
./verify.sh                       # ~20 min: 10 incident scenarios × 2 tools
```
It prints a PASS/FAIL table. Every scenario must pass. See [incident-sim/README.md](incident-sim/README.md).
