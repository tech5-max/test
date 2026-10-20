#!/usr/bin/env python3
"""Prints one clear status line every MONITOR_EVERY seconds while k6 runs.

Reads k6's live metrics stream (the web-dashboard backend, enabled by run.sh /
the container entrypoint) and needs only the Python standard library, so it
works on a headless server. Example line:

[00:50] step 5 | target 1000/s | done 261/s | users 3973 | p95 8.0s p99 12.1s | ok 88.7% |
        5xx 0 · timeout 12 · refused 0 · reset 3 | test-machine 40 ⚠ | 🟡 Under stress
"""
import json
import os
import sys
import time
import urllib.request

PORT = os.environ.get("DASHBOARD_PORT", "5665")
EVERY = float(os.environ.get("MONITOR_EVERY", "10"))
MODE = os.environ.get("TEST_MODE", "stress").lower()
HEALTHY_ERR = float(os.environ.get("HEALTHY_ERROR_RATE") or 0.01)
HEALTHY_P95 = float(os.environ.get("HEALTHY_P95_MS") or 2000)
CRITICAL_ERR = float(os.environ.get("CRITICAL_ERROR_RATE") or 0.01)
MAX_VUS = int(os.environ.get("MAX_VUS") or 5000)
STEP_SECONDS = float((os.environ.get("STRESS_STEP_SECONDS") or "10").rstrip("s"))
START_REQ = float(os.environ.get("STRESS_START_REQUESTS") or 500)
STEP_REQ = float(os.environ.get("STRESS_STEP_REQUESTS") or 500)
if os.environ.get("REQUESTS_PER_SECOND"):
    LOAD_RPS = float(os.environ["REQUESTS_PER_SECOND"])
else:
    LOAD_RPS = float(os.environ.get("REQUESTS_PER_MINUTE") or 0) / 60

CRASH = ["server_5xx", "timeout", "refused", "reset", "unreachable", "tls", "other_network"]
SHOW = [("server_5xx", "5xx"), ("timeout", "timeout"), ("refused", "refused"), ("reset", "reset"),
        ("unreachable", "unreachable"), ("tls", "tls"), ("other_network", "other")]


def ms(v):
    if v is None:
        return "–"
    return f"{v / 1000:.1f}s" if v >= 1000 else f"{round(v)}ms"


def say(text):
    print(text, flush=True)


def events():
    """Yield (event, data) from the k6 stream; wait for k6 to start it."""
    url = f"http://127.0.0.1:{PORT}/events"
    deadline = time.time() + 120
    while True:
        try:
            resp = urllib.request.urlopen(url, timeout=60)
            break
        except Exception:
            if time.time() > deadline:
                say("monitor: k6 live stream not available; no live lines (the report is still written)")
                return
            time.sleep(1)
    ev, data = "message", []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield ev, "\n".join(data)
            ev, data = "message", []
        elif line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def main():
    names, types = [], {}
    agg = {"counter": ["count", "rate"], "gauge": ["value"], "rate": ["rate"],
           "trend": ["avg", "max", "med", "min", "p(90)", "p(95)", "p(99)"]}
    start_ms = None
    window = []
    last_print = time.time()

    def val(arr, metric, key):
        if metric not in names:
            return None
        i = names.index(metric)
        keys = agg.get(types.get(metric), [])
        if i >= len(arr) or arr[i] is None or key not in keys:
            return None
        return arr[i][keys.index(key)]

    def flush():
        if not window:
            return
        snap = window[-1][0]  # latest snapshot's data (window holds (data, period) pairs)
        now_ms = val(snap, "time", "value") or time.time() * 1000
        elapsed = max(0, (now_ms - start_ms) / 1000) if start_ms else 0
        secs = sum(w[1] for w in window)
        counts = {c: sum((val(w[0], f"out_{c}", "count") or 0) for w in window)
                  for c in ["ok", "client_limit", "rate_limited", "other_4xx"] + CRASH}
        done = sum(counts.values())
        server = done - counts["client_limit"]
        crash = sum(counts[c] for c in CRASH)
        err = (server - counts["ok"]) / server if server else 0
        hard = crash / server if server else 0
        p95 = val(snap, "http_req_duration", "p(95)")
        p99 = val(snap, "http_req_duration", "p(99)")
        vus = val(snap, "vus", "value") or 0
        dropped = sum((val(w[0], "dropped_iterations", "count") or 0) for w in window)

        if MODE == "load":
            where, target = "load", LOAD_RPS
        else:
            step = int(elapsed // STEP_SECONDS) + 1
            where, target = f"step {step}", (START_REQ + (step - 1) * STEP_REQ) / STEP_SECONDS
        if done == 0:
            stage = "… waiting for responses"
        elif hard > CRITICAL_ERR:
            stage = "🔴 Critical: server failing"
        elif err <= HEALTHY_ERR and (p95 or 0) <= HEALTHY_P95:
            stage = "🟢 Easily serves"
        else:
            stage = "🟡 Under stress"
        fails = " · ".join(f"{label} {counts[c]}" for c, label in SHOW if counts[c]) or "none"
        gen = ""
        if counts["client_limit"]:
            gen += f" | ⚠ test-machine limit: {counts['client_limit']} failed on this machine"
        if dropped and vus >= MAX_VUS * 0.95:
            gen += f" | ⚠ test machine at MAX_VUS ({MAX_VUS}): {int(dropped)} not sent"
        elif dropped:
            gen += f" | {int(dropped)} not sent (no free user)"
        if counts["rate_limited"]:
            gen += f" | 429 ×{counts['rate_limited']}"
        say(f"[{int(elapsed // 60):02d}:{int(elapsed % 60):02d}] {where} | target {target:.0f}/s | done {done / secs if secs else 0:.0f}/s"
            f" | users {int(vus)} | p95 {ms(p95)} p99 {ms(p99)} | ok {100 * (1 - err):.1f}% | failures: {fails}{gen} | {stage}")
        window.clear()

    period = 2.0
    for ev, raw in events():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if ev == "param":
            agg = d.get("aggregates", agg)
            period = (d.get("period") or 2000) / 1000
        elif ev == "metric":
            for k, v in d.items():
                if k not in names:
                    names.append(k)
                types[k] = v.get("type")
            names.sort()  # k6 orders snapshot arrays by metric name
        elif ev == "start":
            flat = d
            while isinstance(flat, list) and flat:
                flat = flat[0]
            start_ms = flat if isinstance(flat, (int, float)) else time.time() * 1000
        elif ev == "snapshot":
            window.append((d, period))
            if time.time() - last_print >= EVERY:
                flush()
                last_print = time.time()
        elif ev == "stop":
            break
    flush()
    say("monitor: k6 finished, writing the report…")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        sys.exit(0)
