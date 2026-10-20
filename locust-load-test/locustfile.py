"""Locust load test for the fast-dragon public preview GET APIs (logged out).

  load:   fixed rate (REQUESTS_PER_MINUTE / REQUESTS_PER_SECOND) for DURATION
  stress: load grows every STRESS_STEP_SECONDS until the server fails
          (5xx or no response from the server on > CRITICAL_ERROR_RATE of a
          step's requests); slowness alone never stops it

Each user sends 1 request per second, so users = target req/s. Runs headless on
all CPU cores (--processes -1); workers ship their numbers to the master, which
prints a status line every MONITOR_EVERY seconds and writes the report.
Same settings and report layout as the k6 service. See README.md.
"""
import itertools
import math
import os
import sys
import time
import urllib.request

import gevent
import locust.stats as locust_stats
from locust import LoadTestShape, constant_pacing, events, task
from locust.contrib.fasthttp import FastHttpSession, FastHttpUser
from locust.runners import MasterRunner, WorkerRunner

from config import ConfigError, build_steps, level_of, read_config, step_at
from endpoints import AI, PUBLIC
from report import build_report
from stats import CRASH, STATS, Stats, classify, summarize

try:
    CFG = read_config()
except ConfigError as e:
    print(f"✗ Config error: {e}", file=sys.stderr, flush=True)
    sys.exit(2)

STEPS = build_steps(CFG)
ENDPOINTS = [(n, p, "public") for n, p in PUBLIC] + ([(n, p, "ai") for n, p in AI] if CFG["include_ai"] else [])

# Only our own terminal output: switch off Locust's stats tables.
for _fn in ("print_stats", "print_percentile_stats", "print_error_report"):
    setattr(locust_stats, _fn, lambda *a, **k: None)

STATE = {"t0": None, "runner": None, "role": "local", "stopping": False, "started_at": time.time(), "done": False}
_rr = itertools.count()


def elapsed():
    return time.time() - STATE["t0"] if STATE["t0"] else 0.0


def target_rps(step_index):
    return STEPS[step_index - 1]["rps"]


def users_for(rps):
    """Enough users for the rate, each sending at most 1 req/s (rps / users)."""
    return max(1, math.ceil(rps - 1e-9))


def target_users(step_index):
    return users_for(target_rps(step_index))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class ApiUser(FastHttpUser):
    # Users = ceil(target req/s); each paces itself at target / users req/s
    # (≤ 1), so fractional rates like 40 req/min (0.67/s) are sent exactly.
    def wait_time(self):
        rps = target_rps(step_at(STEPS, elapsed()))
        per_user = rps / min(users_for(rps), CFG["max_users"])
        return constant_pacing(1 / per_user)(self)
    network_timeout = CFG["request_timeout"]
    connection_timeout = min(CFG["request_timeout"], 10.0)
    max_retries = 0

    def on_start(self):
        self.ai_client = None
        if CFG["include_ai"]:
            self.ai_client = FastHttpSession(
                base_url=self.host, request_event=self.environment.events.request, user=self,
                network_timeout=CFG["ai_request_timeout"], connection_timeout=min(CFG["ai_request_timeout"], 10.0), max_retries=0,
            )

    @task
    def hit(self):
        name, path, group = ENDPOINTS[next(_rr) % len(ENDPOINTS)]
        step = step_at(STEPS, elapsed())
        STATS.started(step)
        client = self.ai_client if group == "ai" and self.ai_client else self.client
        client.get(path, name=name, context={"step": step, "group": group})


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response=None, context=None, exception=None, **kw):
    if STATE["stopping"] or not context or "step" not in context:
        return  # in flight while the test stops: not a server result
    status = getattr(response, "status_code", 0) or 0
    cls = classify(status, exception)
    STATS.record(context["step"], name, cls, response_time or 0)


@events.cpu_warning.add_listener
def on_cpu_warning(environment, cpu_usage, **kw):
    STATS.mark(step_at(STEPS, elapsed()), cpu_warn=True)


# ---------------------------------------------------------------------------
# Distributed transport: workers → master
# ---------------------------------------------------------------------------
@events.report_to_master.add_listener
def on_report_to_master(client_id, data):
    data["olt"] = STATS.drain()


@events.worker_report.add_listener
def on_worker_report(client_id, data):
    if "olt" in data:
        STATS.merge(data["olt"])


@events.init.add_listener
def on_init(environment, **kw):
    STATE["runner"] = environment.runner
    if isinstance(environment.runner, MasterRunner):
        STATE["role"] = "master"
    elif isinstance(environment.runner, WorkerRunner):
        STATE["role"] = "worker"


@events.test_start.add_listener
def on_test_start(environment, **kw):
    STATE["t0"] = time.time()
    STATE["stopping"] = False
    if STATE["role"] != "worker":
        STATE["started_at"] = time.time()
        _print_plan()
        _probe()


@events.test_stopping.add_listener
def on_test_stopping(environment, **kw):
    STATE["stopping"] = True


@events.quitting.add_listener
def on_quitting(environment, **kw):
    if STATE["role"] == "worker" or STATE["done"]:
        return
    STATE["done"] = True
    _monitor(final=True)
    workers = len(getattr(environment.runner, "clients", {}) or {}) or 1
    build_report(CFG, STEPS, STATS, elapsed(), STATE["started_at"], len(ENDPOINTS), workers)


# ---------------------------------------------------------------------------
# Load shape + stop rule (runs on the master / local runner)
# ---------------------------------------------------------------------------
class Shape(LoadTestShape):
    use_common_options = False

    def tick(self):
        t = self.get_run_time()
        runner = STATE["runner"]
        users_now = getattr(runner, "user_count", 0) if runner else 0
        step = step_at(STEPS, t)
        STATS.mark(step, users=users_now)
        _monitor()

        if CFG["mode"] == "load":
            if t >= CFG["duration"]:
                return None
            users = min(target_users(1), CFG["max_users"])
            return users, max(50, users)

        if t >= CFG["max_duration"]:
            return None
        # The only stop rule: a finished step where the server failed (5xx or no
        # response) on more than CRITICAL_ERROR_RATE of its requests. Judged once
        # the step is over plus one worker report, on the whole step.
        settle = 4.0
        for st in STEPS:
            if st["end"] + settle > t:
                break
            slot = STATS.steps.get(st["index"])
            if slot and summarize(slot)["hard_rate"] > CFG["critical_error_rate"]:
                return None
        users = min(target_users(step), CFG["max_users"])
        return users, max(50, users)


# ---------------------------------------------------------------------------
# Terminal output (master / local)
# ---------------------------------------------------------------------------
_MON = {"last": 0.0, "prev": None}


def _monitor(final=False):
    if STATE["role"] == "worker" or not STATE["t0"]:
        return
    now = time.time()
    if not final and now - _MON["last"] < CFG["monitor_every"]:
        return
    since = _MON["last"] or STATE["t0"]
    _MON["last"] = now
    # Window = what arrived since the last line: diff of totals per class.
    tot = Stats()
    for k, v in STATS.steps.items():
        tot.merge({"steps": {"0": {"counts": dict(v["counts"]), "hist": {str(b): c for b, c in v["hist"].items()},
                                    "started": v["started"], "sum": v["sum"], "max": v["max"], "users": v["users"], "cpu_warn": v["cpu_warn"]}}})
    cur = tot.steps.get(0)
    if cur is None:
        return
    prev = _MON["prev"]
    win = {"counts": cur["counts"].copy(), "hist": cur["hist"].copy(), "sum": cur["sum"], "max": cur["max"], "started": 0, "users": cur["users"], "cpu_warn": False}
    if prev:
        win["counts"].subtract(prev["counts"])
        win["hist"].subtract(prev["hist"])
        win["sum"] -= prev["sum"]
    _MON["prev"] = {"counts": cur["counts"].copy(), "hist": cur["hist"].copy(), "sum": cur["sum"]}
    win["counts"] = +win["counts"]
    win["hist"] = +win["hist"]
    s = summarize(win)
    secs = max(1e-6, now - since)
    e = elapsed()
    step = step_at(STEPS, e)
    if CFG["mode"] == "load":
        where, target = "load", CFG["load_rpm"] / 60
    else:
        where, target = f"step {step}", STEPS[step - 1]["rps"]
    users = getattr(STATE["runner"], "user_count", 0) if STATE["runner"] else 0
    if s["reqs"] == 0:
        stage = "… waiting for responses"
    else:
        lvl = level_of(CFG, s["err_rate"], s["latency"]["p(95)"] or 0, s["hard_rate"])
        stage = {"healthy": "🟢 Easily serves", "stressed": "🟡 Under stress", "critical": "🔴 Critical: server failing"}[lvl]
    labels = [("server_5xx", "5xx"), ("timeout", "timeout"), ("refused", "refused"), ("reset", "reset"),
              ("unreachable", "unreachable"), ("tls", "tls"), ("other_network", "other")]
    fails = " · ".join(f"{lab} {s['counts'][c]}" for c, lab in labels if s["counts"][c]) or "none"
    extra = ""
    if s["counts"]["client_limit"]:
        extra += f" | ⚠ test-machine limit: {s['counts']['client_limit']} failed on this machine"
    if s["counts"]["rate_limited"]:
        extra += f" | 429 ×{s['counts']['rate_limited']}"
    if users >= CFG["max_users"] and users_for(target) > CFG["max_users"] and s["reqs"] / secs < target * 0.95:
        extra += f" | ⚠ users at MAX_VUS ({CFG['max_users']}) can't send the full load"
    def ms(v):
        return "–" if v is None else (f"{v / 1000:.1f}s" if v >= 1000 else f"{round(v)}ms")
    print(f"[{int(e // 60):02d}:{int(e % 60):02d}] {where} | target {target:.0f}/s | done {s['reqs'] / secs:.0f}/s | users {users}"
          f" | p95 {ms(s['latency']['p(95)'])} p99 {ms(s['latency']['p(99)'])} | ok {100 * (1 - s['err_rate']):.1f}%"
          f" | failures: {fails}{extra} | {stage}", flush=True)


def _fmt_dur(sec):
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"


def _print_plan():
    print(f"▶ Target {CFG['base_url']} · mode {CFG['mode']} · AI {'on' if CFG['include_ai'] else 'off'} · {len(ENDPOINTS)} endpoints · Locust", flush=True)
    if CFG["mode"] == "load":
        print(f"▶ Fixed {CFG['load_rpm']:.0f} req/min ({CFG['load_rpm'] / 60:.2f} req/s = {target_users(1)} users at {target_rps(1) / target_users(1):.2f} req/s each) for {_fmt_dur(CFG['duration'])}", flush=True)
    else:
        s1 = STEPS[0]
        print(f"▶ Stress: {s1['requests']:.0f} requests in the first {CFG['step_seconds']:.0f}s ({s1['rps']:.0f} req/s), "
              f"+{CFG['step_requests']:.0f} every {CFG['step_seconds']:.0f}s. Stops only when the server fails (5xx or no response) "
              f"on more than {CFG['critical_error_rate'] * 100:.2f}% of a step's requests, or after {_fmt_dur(CFG['max_duration'])} "
              f"(up to {STEPS[-1]['rps']:.0f} req/s). Slow responses don't stop it.", flush=True)


def _probe():
    try:
        with urllib.request.urlopen(f"{CFG['base_url']}/api/top-coins", timeout=30) as r:
            limit = r.headers.get("RateLimit-Limit")
            status = r.status
    except urllib.error.HTTPError as e:
        limit, status = e.headers.get("RateLimit-Limit"), e.code
    except Exception as e:
        print(f"⚠ Probe failed: {e}. Is TARGET_BASE_URL reachable?", flush=True)
        return
    if limit:
        print(f"⚠ Server rate limit is ON: {limit} requests per window per IP. You'll get 429s, not real capacity. "
              "Set RATE_LIMIT_ENABLED=false on the server.", flush=True)
    else:
        print(f"▶ Probe OK (HTTP {status}); server rate limiter is off.", flush=True)
