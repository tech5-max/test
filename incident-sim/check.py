#!/usr/bin/env python3
"""Checks one load-test run (k6 or Locust) against the expected outcome of its
incident scenario. Usage: check.py <scenario> <tool> <run-dir> <terminal-log>
Prints 'PASS' or 'FAIL: reason…' and exits 0/1."""
import glob
import json
import os
import sys

scenario, tool, run_dir, log_path = sys.argv[1:5]
CRASH = ["server_5xx", "timeout", "refused", "reset", "unreachable", "tls", "other_network"]
problems = []

files = sorted(glob.glob(os.path.join(run_dir, "*.json")))
mds = sorted(glob.glob(os.path.join(run_dir, "*.md")))
if not files or not mds:
    print("FAIL: no report written")
    sys.exit(1)
r = json.load(open(files[-1]))
log = open(log_path, errors="replace").read()

# Normalise k6 / Locust field names.
steps = r.get("steps", [])
lvl = lambda s: s.get("level")
limited = lambda s: s.get("generatorLimited", s.get("generator_limited", False))
counts = lambda s: s.get("counts", {})
totals = r.get("totals", {})
crit = [s for s in steps if lvl(s) == "critical"]
result = r.get("result", {})


def expect(cond, msg):
    if not cond:
        problems.append(msg)


# Every run: the headless terminal output and the markdown report.
expect("[00:" in log, "no 10-second status lines in the terminal")
expect("Load Test Report" in log, "no final result in the terminal")
expect(os.path.getsize(mds[-1]) > 200, "markdown report is empty")
# Cancelled requests (in flight when the tool stops) must never be counted.
expect(totals.get("other_network", 0) == 0, f"other_network={totals.get('other_network')} (cancelled requests counted?)")

if scenario in ("healthy-load", "fractional-load"):
    expect(result.get("level") == "healthy", f"load verdict {result.get('level')} != healthy")
    s = steps[0] if steps else {}
    target = s.get("rps", 0)
    # Requests started during the run vs what the rate × duration should give.
    sent = s.get("started", 0)
    planned = s.get("requests", 0)
    expect(planned and abs(sent - planned) / planned < 0.10, f"sent {sent} requests vs planned {planned:.0f} (target {target:.2f}/s)")
elif scenario in ("healthy", "slow", "stray5xx", "ratelimit", "generator"):
    expect(not crit, f"stopped as critical at step {crit[0]['index'] if crit else '-'} but the server never failed")
    expect("Reached STRESS_MAX_DURATION" in json.dumps(result), "did not run to STRESS_MAX_DURATION")
    if scenario == "slow":
        expect(any(lvl(s) == "stressed" for s in steps), "slowness never showed as 🟡")
    if scenario == "stray5xx":
        expect(totals.get("server_5xx") == 2, f"expected exactly 2 stray 5xx, got {totals.get('server_5xx')}")
    if scenario == "ratelimit":
        expect(totals.get("rate_limited", 0) > 0, "no 429s recorded")
        expect("rate limit is ON" in log, "no rate-limiter warning at startup")
    if scenario == "generator":
        expect(any(limited(s) for s in steps), "generator limit not flagged")
        expect("Not measured" in json.dumps(result), "healthy stage should be 'Not measured' when the generator was the limit")
else:
    want = {"5xx": "server_5xx", "timeout": "timeout", "refused": "refused", "reset": "reset"}[scenario]
    expect(bool(crit), "never reached 🔴 although the server failed")
    if crit:
        c = counts(crit[0])
        top = max(CRASH, key=lambda k: c.get(k, 0))
        expect(top == want, f"main failure type {top} != {want}")
        expect(crit[0]["index"] in (2, 3), f"crash detected at step {crit[0]['index']} (expected 2 or 3)")
        expect(steps[-1]["index"] == crit[0]["index"], "steps after the crash step were reported")
    expect("Stopped automatically" in json.dumps(result), "run was not stopped automatically")

print("PASS" if not problems else "FAIL: " + "; ".join(problems))
sys.exit(0 if not problems else 1)
