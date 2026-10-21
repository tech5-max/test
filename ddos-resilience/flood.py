#!/usr/bin/env python3
"""HTTP flood from one machine against YOUR OWN server, to test DDoS defenses
(nginx rate limit + fail2ban). In one run it measures: how many requests the
server accepted, and after how long / how many requests it either became
inaccessible (overload) or started dropping our connection (fail2ban blocked us).

Standard library only; runs in Docker via run.sh. One real source IP — correct
for a fail2ban demo, since fail2ban bans by the real IP.
"""
import asyncio
import os
import socket
import ssl
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

TARGET = os.environ.get("TARGET_BASE_URL", "").rstrip("/")
ENDPOINT = os.environ.get("ENDPOINT", "/api/top-coins")
RPS = float(os.environ.get("REQUESTS_PER_SECOND", "1000"))
DURATION = min(float(os.environ.get("DURATION", "60")), 600)
PHASE = os.environ.get("PHASE_LABEL", "flood")
BAN_CONFIRM = float(os.environ.get("BAN_CONFIRM_SECONDS", "5"))
CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.environ.get("READ_TIMEOUT", "15"))
MONITOR_EVERY = int(float(os.environ.get("MONITOR_EVERY", "5")))
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "3000"))
DEGRADE_P95_MS = float(os.environ.get("DEGRADE_P95_MS", "5000"))
ALLOW_LOCAL = os.environ.get("ALLOW_LOCAL", "false").lower() == "true"

CLASSES = ["ok", "rate_limited", "server_5xx", "slow", "blocked", "error"]


def die(msg):
    print(f"✗ {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


if not TARGET.startswith(("http://", "https://")):
    die('TARGET_BASE_URL is required, e.g. "https://api.ozak.dev"')
parts = urlsplit(TARGET)
HOST = parts.hostname
PORT = parts.port or (443 if parts.scheme == "https" else 80)
USE_TLS = parts.scheme == "https"


def _is_local(h):
    if h in ("localhost",):
        return True
    try:
        ip = socket.getaddrinfo(h, None)[0][4][0]
    except socket.gaierror:
        return False
    if ip in ("::1",) or ip.startswith(("127.", "10.", "192.168.")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except ValueError:
            return False
    return False


if _is_local(HOST) and not ALLOW_LOCAL:
    die(f"{HOST} looks private/loopback. Refusing. Set ALLOW_LOCAL=true only for local testing.")

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
REQUEST = (f"GET {ENDPOINT} HTTP/1.1\r\nHost: {HOST}\r\n"
           "User-Agent: ozak-ddos-resilience-test\r\nConnection: close\r\nAccept: */*\r\n\r\n").encode()

# Per-second buckets, filled as requests complete.
sec_counts = defaultdict(Counter)   # second -> class -> count
sec_lat = defaultdict(list)         # second -> [latency_ms]
T0 = None


async def one_request():
    # 'blocked' = the TCP connection itself failed (refused/reset/connect-timeout):
    #   the firewall (fail2ban) or a dead server dropped us.
    # 'slow'    = connected, but no response within READ_TIMEOUT: the server is
    #   alive but overloaded. This is NOT a firewall block.
    t0 = time.perf_counter()
    writer = None
    connected = False
    try:
        conn = asyncio.open_connection(HOST, PORT, ssl=SSL_CTX if USE_TLS else None,
                                       server_hostname=HOST if USE_TLS else None)
        reader, writer = await asyncio.wait_for(conn, timeout=CONNECT_TIMEOUT)
        connected = True
        writer.write(REQUEST)
        await asyncio.wait_for(writer.drain(), timeout=READ_TIMEOUT)
        line = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
        ms = (time.perf_counter() - t0) * 1000
        if not line:
            return "blocked", ms  # connected then closed with no bytes = reset/drop
        try:
            status = int(line.split(b" ")[1])
        except (IndexError, ValueError):
            return "error", ms
        if 200 <= status < 300:
            return "ok", ms
        if status == 429:
            return "rate_limited", ms
        if status >= 500:
            return "server_5xx", ms
        return "ok", ms  # other 3xx/4xx: the server answered, so not blocked
    except asyncio.TimeoutError:
        return ("slow" if connected else "blocked"), (time.perf_counter() - t0) * 1000
    except (ConnectionResetError, BrokenPipeError):
        return "blocked", (time.perf_counter() - t0) * 1000
    except OSError:
        # connected then failed = reset/drop; never connected = refused/unreachable
        return "blocked", (time.perf_counter() - t0) * 1000
    except Exception:
        return "error", (time.perf_counter() - t0) * 1000
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


def _record(cls, ms):
    s = int(time.time() - T0)
    sec_counts[s][cls] += 1
    if len(sec_lat[s]) < 3000:
        sec_lat[s].append(ms)


async def worker(sem):
    async with sem:
        cls, ms = await one_request()
    _record(cls, ms)


async def monitor_loop(stop):
    while not stop.is_set():
        await asyncio.sleep(MONITOR_EVERY)
        now = int(time.time() - T0)
        _status(now)


def _p95(vals):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def _status(now):
    window = range(max(0, now - MONITOR_EVERY), now)
    agg = Counter()
    lats = []
    for s in window:
        agg.update(sec_counts.get(s, {}))
        lats += sec_lat.get(s, [])
    secs = max(1, len(list(window)))
    done = sum(agg.values())
    ok = agg["ok"]
    alive = ok + agg["rate_limited"] + agg["server_5xx"] + agg["slow"]
    p95 = _p95(lats)
    if done == 0:
        return print(f"[{now:3d}s] warming up…", flush=True)
    if agg["blocked"] and alive == 0:
        state = "🔴 BLOCKED (connections dropped — server/firewall cut us off)"
    elif agg["slow"] or (p95 and p95 > DEGRADE_P95_MS):
        state = "🟠 DEGRADED (server alive but overloaded/slow)"
    elif agg["rate_limited"]:
        state = "🟡 rate-limited (429s)"
    else:
        state = "🟢 serving"
    pm = f"{p95/1000:.1f}s" if p95 and p95 >= 1000 else (f"{p95:.0f}ms" if p95 else "–")
    print(f"[{now:3d}s] sent {done/secs:.0f}/s | ok {ok/secs:.0f}/s | 429 {agg['rate_limited']/secs:.0f}/s"
          f" | slow {agg['slow']/secs:.0f}/s | blocked {agg['blocked']/secs:.0f}/s | p95 {pm} | {state}", flush=True)


async def main():
    global T0
    print("=" * 66, flush=True)
    print(f"  DDoS resilience test — target: {TARGET}", flush=True)
    print(f"  Phase: {PHASE} · rate: {RPS:.0f} req/s · duration: {DURATION:.0f}s · one source IP", flush=True)
    print("  Run ONLY against a server you own. This measures how the server defends.", flush=True)
    print("=" * 66, flush=True)

    T0 = time.time()
    end = T0 + DURATION
    interval = 1.0 / RPS if RPS > 0 else 0
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    stop = asyncio.Event()
    mon = asyncio.create_task(monitor_loop(stop))
    tasks = set()
    next_at = time.time()
    while time.time() < end:
        now = time.time()
        if now >= next_at and not sem.locked():
            t = asyncio.create_task(worker(sem))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
            next_at += interval
        else:
            await asyncio.sleep(min(0.001, max(0, next_at - now)))
    stop.set()
    if tasks:
        await asyncio.wait(tasks, timeout=CONNECT_TIMEOUT + 2)
    mon.cancel()

    _summarize()


def _summarize():
    total = Counter()
    secs = sorted(sec_counts)
    samples = []
    for s in secs:
        c = sec_counts[s]
        total.update(c)
        samples.append({"t": s, **{k: c.get(k, 0) for k in CLASSES}, "p95": _p95(sec_lat.get(s, []))})

    # Time to block: first second where the server stopped answering (only
    # blocked, no responses) and stayed that way for BAN_CONFIRM seconds.
    time_to_block = None
    reqs_before_block = None
    run_total = 0
    blocked_run_start = None
    for s in samples:
        answered = s["ok"] + s["rate_limited"] + s["server_5xx"]
        alive = answered + s["slow"]
        sent = sum(s[k] for k in CLASSES)
        if s["blocked"] > 0 and alive == 0 and sent >= 3:
            if blocked_run_start is None:
                blocked_run_start = s["t"]
                reqs_at_block = run_total
            if s["t"] - blocked_run_start + 1 >= BAN_CONFIRM and time_to_block is None:
                time_to_block = blocked_run_start
                reqs_before_block = reqs_at_block
        else:
            blocked_run_start = None
        run_total += answered

    peak = max((sum(s[k] for k in CLASSES) for s in samples), default=0)
    accepted = total["ok"] + total["rate_limited"] + total["server_5xx"]
    blocked_after = 0
    if time_to_block is not None:
        blocked_after = sum(s["blocked"] for s in samples if s["t"] >= time_to_block)

    result = {
        "phase": PHASE, "target": TARGET, "endpoint": ENDPOINT, "rate_target": RPS,
        "duration": time.time() - T0, "peak_rps": peak, "degrade_p95_ms": DEGRADE_P95_MS,
        "totals": {k: total[k] for k in CLASSES}, "total_requests": sum(total.values()),
        "accepted": accepted, "time_to_block": time_to_block,
        "requests_before_block": reqs_before_block, "blocked_after": blocked_after,
        "samples": samples, "started_at": T0,
    }
    from report import write_report
    write_report(result)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n▶ Stopped.", flush=True)
