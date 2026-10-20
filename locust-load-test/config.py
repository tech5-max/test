"""Reads and validates the env (same variable names as the k6 service) and
works out the load steps. Shared by locustfile.py and report.py."""
import os
import re


class ConfigError(Exception):
    pass


def _env(name, default=None):
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _num(name, default, minimum=0.0):
    raw = _env(name)
    if raw is None:
        return default
    try:
        n = float(raw)
    except ValueError:
        raise ConfigError(f'{name} must be a number (got "{raw}")')
    if n < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {raw})")
    return n


def parse_duration(name, value):
    """'90s', '2m', '1m30s', '1h', or a bare number of seconds → seconds."""
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    parts = re.findall(r"(\d+(?:\.\d+)?)(h|m|s)", value)
    if not parts or "".join(a + b for a, b in parts) != value:
        raise ConfigError(f'{name} must be a duration like 30s, 2m, 1m30s (got "{value}")')
    return sum(float(a) * {"h": 3600, "m": 60, "s": 1}[b] for a, b in parts)


def read_config():
    base = _env("TARGET_BASE_URL", "")
    if not re.match(r"^https?://[^/]+", base):
        raise ConfigError('TARGET_BASE_URL is required, e.g. "https://api.ozak.dev"')
    mode = _env("TEST_MODE", "stress").lower()
    if mode not in ("load", "stress"):
        raise ConfigError(f'TEST_MODE must be "load" or "stress" (got "{mode}")')

    cfg = {
        "base_url": base.rstrip("/"),
        "mode": mode,
        "include_ai": _env("INCLUDE_AI_ANALYSIS", "false").lower() == "true",
        "healthy_error_rate": _num("HEALTHY_ERROR_RATE", 0.01),
        "healthy_p95_ms": _num("HEALTHY_P95_MS", 2000, 1),
        "critical_error_rate": _num("CRITICAL_ERROR_RATE", 0.01),
        # Max parallel users (like k6's MAX_VUS). Normally users = ceil(req/s),
        # each at ≤ 1 req/s; when capped, each user paces faster to keep the rate.
        "max_users": int(_num("MAX_VUS", 5000, 1)),
        "request_timeout": parse_duration("REQUEST_TIMEOUT", _env("REQUEST_TIMEOUT", "15s")),
        "ai_request_timeout": parse_duration("AI_REQUEST_TIMEOUT", _env("AI_REQUEST_TIMEOUT", "60s")),
        "monitor_every": _num("MONITOR_EVERY", 10, 1),
    }
    if mode == "load":
        rps = _env("REQUESTS_PER_SECOND")
        rpm = _env("REQUESTS_PER_MINUTE")
        if rps is None and rpm is None:
            raise ConfigError("load mode needs REQUESTS_PER_MINUTE (or REQUESTS_PER_SECOND)")
        cfg["load_rpm"] = _num("REQUESTS_PER_SECOND", 0, 1) * 60 if rps is not None else _num("REQUESTS_PER_MINUTE", 0, 1)
        cfg["duration"] = parse_duration("DURATION", _env("DURATION", "2m"))
    else:
        cfg["start_requests"] = _num("STRESS_START_REQUESTS", 500, 1)
        cfg["step_requests"] = _num("STRESS_STEP_REQUESTS", 500, 1)
        cfg["step_seconds"] = parse_duration("STRESS_STEP_SECONDS", _env("STRESS_STEP_SECONDS", "10"))
        if cfg["step_seconds"] < 5:
            raise ConfigError("STRESS_STEP_SECONDS must be at least 5")
        cfg["max_duration"] = parse_duration("STRESS_MAX_DURATION", _env("STRESS_MAX_DURATION", "30m"))
        cfg["max_steps"] = int(cfg["max_duration"] // cfg["step_seconds"])
        if cfg["max_steps"] < 1:
            raise ConfigError("STRESS_MAX_DURATION must be at least one STRESS_STEP_SECONDS")
    return cfg


def build_steps(cfg):
    """Measurement windows; every request is tagged with the step it starts in."""
    if cfg["mode"] == "load":
        d = cfg["duration"]
        return [{"index": 1, "requests": cfg["load_rpm"] * d / 60, "rps": cfg["load_rpm"] / 60, "start": 0.0, "end": d}]
    steps = []
    for n in range(1, cfg["max_steps"] + 1):
        req = cfg["start_requests"] + (n - 1) * cfg["step_requests"]
        steps.append({"index": n, "requests": req, "rps": req / cfg["step_seconds"],
                      "start": (n - 1) * cfg["step_seconds"], "end": n * cfg["step_seconds"]})
    return steps


def step_at(steps, elapsed):
    for s in steps:
        if s["start"] <= elapsed < s["end"]:
            return s["index"]
    return steps[-1]["index"]


def level_of(cfg, err_rate, p95, hard_rate):
    """🔴 only when the server fails (5xx or no response); slowness alone is 🟡."""
    if hard_rate > cfg["critical_error_rate"]:
        return "critical"
    if err_rate <= cfg["healthy_error_rate"] and p95 <= cfg["healthy_p95_ms"]:
        return "healthy"
    return "stressed"
