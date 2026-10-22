#!/usr/bin/env python3
"""Scan the realtime combo reports for real problems, without false alarms.
Distinguishes:
  - CONFIG/SERVER: an endpoint that fails even in a cache-ON (low-load) combo —
    a server or env issue (e.g. a DB/warehouse not configured), not the harness.
  - CACHE-STUB REGRESSION (bug): an endpoint that works cache-on but fails cache-off.
  - LOAD: server failures that rise with load.
Rate-limit 429s are reported as info (the ceiling is 60/min logged out, 300/min
with a session cookie — so 0 can be correct). Stdlib only."""
import glob
import json
import os
import sys

reports = sys.argv[1] if len(sys.argv) > 1 else "reports"


def newest(folder):
    js = glob.glob(os.path.join(folder, "*.json"))
    return max(js, key=os.path.getmtime) if js else None


runs = {}
for entry in sorted(os.listdir(reports)):
    folder = os.path.join(reports, entry)
    if os.path.isdir(folder):
        j = newest(folder)
        if j:
            runs[entry] = json.load(open(j))

if not runs:
    print("  (no combo reports found)")
    sys.exit(0)


def c5xx(e):
    return (e.get("counts") or {}).get("server_5xx", 0)


def has_5xx(e):  # erroring (not just slow/timeout) on most requests
    return e.get("reqs", 0) >= 3 and c5xx(e) >= e["reqs"] * 0.5


cache_on = [c for c in runs if "cache-on" in c]
# endpoints that 5xx even in a cache-ON combo → server/config, not load/cache
config_broken = {}
for c in cache_on:
    for e in runs[c].get("endpoints", []):
        if has_5xx(e):
            config_broken.setdefault(e["name"], e.get("svc", "?"))

bugs, warns, infos = [], [], []

for combo, d in runs.items():
    s = d.get("summary", {})
    tot = d.get("totals", {})
    eps = d.get("endpoints", [])
    # separate real errors (5xx) from slowness (timeouts); only unexplained 5xx is a bug
    fivexx = sum(c5xx(e) for e in eps if e["name"] not in config_broken)
    timeouts = sum((e.get("counts") or {}).get("timeout", 0) for e in eps)
    print(f"  {combo}: {s.get('requests',0)} reqs · success {s.get('successRate',0)*100:.1f}% · "
          f"p95 {round(s.get('p95') or 0)}ms · 429s {tot.get('rate_limited',0)} · "
          f"5xx {sum(c5xx(e) for e in eps)} (config {sum(c5xx(e) for e in eps if e['name'] in config_broken)}) · "
          f"timeouts {timeouts}")
    # a cache-off endpoint that 5xx's (not just times out) while cache-on is clean = stub regression
    if "cache-off" in combo:
        for e in eps:
            if has_5xx(e) and e["name"] not in config_broken:
                bugs.append(f"{combo}: '{e['name']}' 5xx cache-off but clean cache-on — cache-stub regression")
    if fivexx > max(3, 0.05 * s.get("requests", 1)):
        bugs.append(f"{combo}: {fivexx} unexplained 5xx errors (not timeouts) — investigate")
    if timeouts > max(5, 0.1 * s.get("requests", 1)):
        warns.append(f"{combo}: {timeouts} timeouts (>req-timeout) — capacity: heavy endpoints too slow "
                     "at this rate on this many replicas / cold cache, not a code bug")

for name, svc in sorted(config_broken.items()):
    warns.append(f"'{name}' ({svc}) 5xx even cache-on → server/env config, not the harness "
                 "(e.g. WAREHOUSE_CLICKHOUSE_* unset for perps/derivatives)")

def p95(c): return (runs.get(c, {}).get("summary") or {}).get("p95")
def thru(c): return (runs.get(c, {}).get("summary") or {}).get("peakThroughput")
if p95("single-cache-on") and p95("single-cache-off"):
    infos.append(f"cache effect (single): p95 on={round(p95('single-cache-on'))}ms vs off={round(p95('single-cache-off'))}ms")
if thru("single-cache-on") and thru("multi-cache-on"):
    infos.append(f"scaling (cache-on): throughput single={thru('single-cache-on')}/s vs multi={thru('multi-cache-on')}/s")

for b in bugs:
    print(f"  🔴 BUG   {b}")
for w in warns:
    print(f"  🟡 CONFIG {w}")
for i in infos:
    print(f"  ℹ️  {i}")

print()
if bugs:
    print(f"  ❌ {len(bugs)} real bug(s) found.")
    sys.exit(1)
print(f"  ✅ No harness/switch bugs. ({len(warns)} server/env config note(s).)")
