"""Request classification and mergeable stats (per step and per endpoint).

Workers record into STATS and ship deltas to the master every few seconds
(Locust's report_to_master / worker_report events); the master merges them.
Latencies go into log-scale buckets (~1% wide), so percentiles merge exactly
across processes without keeping every sample.
"""
import errno
import math
import socket
import ssl
from collections import Counter

# Same classes as the k6 service (test/load-test/src/classes.js).
CLASSES = ["ok", "server_5xx", "timeout", "refused", "reset", "unreachable", "tls",
           "other_network", "client_limit", "rate_limited", "other_4xx"]
CRASH = ["server_5xx", "timeout", "refused", "reset", "unreachable", "tls", "other_network"]
LABELS = {
    "ok": "OK", "server_5xx": "server errors (5xx)", "timeout": "timeouts (server didn't answer in time)",
    "refused": "connection refused", "reset": "connection reset/dropped", "unreachable": "server unreachable",
    "tls": "TLS handshake failures", "other_network": "other network errors",
    "client_limit": "test-machine limit (sockets/files/DNS)", "rate_limited": "rate-limited (429)", "other_4xx": "other 4xx",
}

_CLIENT_ERRNO = {errno.EMFILE, errno.ENFILE, errno.EADDRNOTAVAIL, errno.ENOBUFS, errno.EADDRINUSE}
_CLIENT_TEXT = ("too many open files", "cannot assign requested address", "no buffer space",
                "address already in use", "name or service not known", "temporary failure in name resolution",
                "nodename nor servname")


def _chain(exc):
    """The exception plus everything it wraps (geventhttpclient wraps the real cause)."""
    seen = []
    while exc is not None and exc not in seen and len(seen) < 6:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__ or (exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else None)
    return seen


def classify(status, exception):
    """Map one request result to exactly one class."""
    if exception is None or status:
        if 200 <= status < 300:
            return "ok"
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "server_5xx"
        if status:
            return "other_4xx"
    chain = _chain(exception)
    text = " ".join(f"{type(e).__name__} {e}" for e in chain).lower()
    for e in chain:
        if isinstance(e, socket.gaierror) or (isinstance(e, OSError) and e.errno in _CLIENT_ERRNO):
            return "client_limit"
    if any(t in text for t in _CLIENT_TEXT):
        return "client_limit"
    for e in chain:
        if isinstance(e, ConnectionRefusedError) or (isinstance(e, OSError) and e.errno == errno.ECONNREFUSED):
            return "refused"
    for e in chain:
        if isinstance(e, (ConnectionResetError, BrokenPipeError)) or (isinstance(e, OSError) and e.errno in (errno.ECONNRESET, errno.EPIPE)):
            return "reset"
    if "connection closed" in text or "connectionclosed" in text or "reset by peer" in text or "broken pipe" in text:
        return "reset"
    for e in chain:
        if isinstance(e, ssl.SSLError):
            return "tls"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    for e in chain:
        if isinstance(e, OSError) and e.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return "unreachable"
    return "other_network"


def bucket(ms):
    return 0 if ms < 1 else int(round(math.log(ms) / math.log(1.01)))


def bucket_ms(b):
    return 0.0 if b == 0 else 1.01 ** b


def _new():
    return {"counts": Counter(), "hist": Counter(), "started": 0, "sum": 0.0, "max": 0.0, "users": 0, "cpu_warn": False}


class Stats:
    def __init__(self):
        self.steps = {}
        self.endpoints = {}

    def _slot(self, table, key):
        if key not in table:
            table[key] = _new()
        return table[key]

    def started(self, step):
        self._slot(self.steps, step)["started"] += 1

    def record(self, step, name, cls, ms):
        for slot in (self._slot(self.steps, step), self._slot(self.endpoints, name)):
            slot["counts"][cls] += 1
            slot["hist"][bucket(ms)] += 1
            slot["sum"] += ms
            slot["max"] = max(slot["max"], ms)

    def mark(self, step, users=None, cpu_warn=False):
        slot = self._slot(self.steps, step)
        if users is not None:
            slot["users"] = max(slot["users"], users)
        if cpu_warn:
            slot["cpu_warn"] = True

    # -- transport between processes ------------------------------------------
    def drain(self):
        """Return everything recorded since the last drain (as plain data) and reset."""
        out = {"steps": {}, "endpoints": {}}
        for tname in ("steps", "endpoints"):
            for k, v in getattr(self, tname).items():
                out[tname][str(k)] = {"counts": dict(v["counts"]), "hist": {str(b): c for b, c in v["hist"].items()},
                                      "started": v["started"], "sum": v["sum"], "max": v["max"],
                                      "users": v["users"], "cpu_warn": v["cpu_warn"]}
        self.steps, self.endpoints = {}, {}
        return out

    def merge(self, data):
        for tname, cast in (("steps", int), ("endpoints", str)):
            table = getattr(self, tname)
            for k, v in data.get(tname, {}).items():
                slot = self._slot(table, cast(k))
                slot["counts"].update(v["counts"])
                slot["hist"].update({int(b): c for b, c in v["hist"].items()})
                slot["started"] += v["started"]
                slot["sum"] += v["sum"]
                slot["max"] = max(slot["max"], v["max"])
                slot["users"] = max(slot["users"], v["users"])
                slot["cpu_warn"] = slot["cpu_warn"] or v["cpu_warn"]


def summarize(slot):
    """Numbers for one step/endpoint slot. Server rates leave out client_limit."""
    counts = slot["counts"]
    reqs = sum(counts.values())
    server = reqs - counts.get("client_limit", 0)
    crash = sum(counts.get(c, 0) for c in CRASH)
    return {
        "reqs": reqs,
        "counts": {c: counts.get(c, 0) for c in CLASSES},
        "crash": crash,
        "err_rate": (server - counts.get("ok", 0)) / server if server else 0.0,
        "hard_rate": crash / server if server else 0.0,
        "client_limit_rate": counts.get("client_limit", 0) / reqs if reqs else 0.0,
        "latency": latency(slot["hist"], slot["sum"], slot["max"]),
        "started": slot["started"],
        "users": slot["users"],
        "cpu_warn": slot["cpu_warn"],
    }


def latency(hist, total_ms, max_ms):
    n = sum(hist.values())
    out = {"avg": total_ms / n if n else None, "max": max_ms if n else None}
    if not n:
        return {**out, **{k: None for k in ("med", "p(85)", "p(90)", "p(95)", "p(99)", "p(99.9)")}}
    ordered = sorted(hist.items())
    for key, q in (("med", 0.5), ("p(85)", 0.85), ("p(90)", 0.90), ("p(95)", 0.95), ("p(99)", 0.99), ("p(99.9)", 0.999)):
        target, seen = q * n, 0
        for b, c in ordered:
            seen += c
            if seen >= target:
                out[key] = min(bucket_ms(b), max_ms)
                break
    return out


STATS = Stats()
