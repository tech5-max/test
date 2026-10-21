# DDoS resilience test (HTTP flood + fail2ban demo)

Floods **your own** server with HTTP requests from one machine and measures how it defends: how many requests it accepted, and after how long / how many requests it either slowed down (overload) or **blocked you** (fail2ban / firewall dropped the connection). Runs entirely in **Docker**.

> Run this **only against a server you own**, and **from a separate machine** — never on the server itself, or you'll block your own SSH. The tool refuses private/loopback targets unless `ALLOW_LOCAL=true`.

## Install and run
Only Docker is needed.
```bash
cd ddos-resilience
cp .env.example .env     # set TARGET_BASE_URL to your server
./run.sh                 # builds the image the first time, then floods
```

## The two-phase defense report
Run once with fail2ban off, once with it on, then combine:
```bash
# Phase 1 — fail2ban stopped on the server:
PHASE_LABEL=without-fail2ban ./run.sh
# Phase 2 — fail2ban started on the server:
PHASE_LABEL=with-fail2ban ./run.sh
# Before/after in one table:
./run.sh --combine
```
See [../fail2ban-guide/FAIL2BAN-SETUP.md](../fail2ban-guide/FAIL2BAN-SETUP.md) to set up fail2ban on the server.

## What it shows
Live, every 5 seconds:
```
[ 15s] sent 1000/s | ok 420/s | 429 60/s | slow 0/s | blocked 520/s | p95 180ms | 🔴 BLOCKED
```
At the end, a verdict and `reports/ddos-<phase>-<time>.md`:
- 🟢 **Held up** — served the whole flood, no block
- 🟠 **Degraded** — alive but slow / erroring under load (add fail2ban)
- 🔴 **Server blocked us** — connections dropped after N requests (~T seconds): fail2ban / firewall worked

## Request classes
| Class | Meaning |
|---|---|
| `ok` | server answered 2xx |
| `429` | the server's own rate limiter rejected it (first line of defense; server stayed up) |
| `slow` | connected, but no answer within `READ_TIMEOUT` — alive but overloaded, **not** a block |
| `blocked` | TCP connection refused / reset / connect-timeout — the firewall (fail2ban) dropped us |

## Settings (`.env`)
`TARGET_BASE_URL`, `ENDPOINT`, `REQUESTS_PER_SECOND`, `DURATION` (≤ 600s), `PHASE_LABEL`, `BAN_CONFIRM_SECONDS`, `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `DEGRADE_P95_MS`, `MONITOR_EVERY`, `MAX_INFLIGHT`, `ALLOW_LOCAL`. See `.env.example`.

## Note on "multiple IPs"
This uses one real source IP, which is correct for a fail2ban demo — fail2ban bans by real IP, so one IP proves the block. You cannot genuinely send traffic from many real IPs out of one machine for HTTP: header tricks (`X-Forwarded-For`) only fool an app-level limiter, not fail2ban; real rotation needs a proxy pool / many VMs / an owned IPv6 range. A true many-IP botnet is what upstream/Cloudflare DDoS protection is for.
