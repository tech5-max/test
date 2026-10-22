#!/usr/bin/env python3
"""Asserts the eon 4-combo harness produced correct reports. Stdlib only; runs in
Docker with the reports dir mounted at /run.

Usage: check.py <reports_dir> <combo1,combo2,...>
Checks, for each combo: a JSON report exists, has a non-empty per-endpoint list
with p95 fields, and a summary with requests > 0. Then checks the consolidated
report: it exists, names every combo, and its per-endpoint table has one row per
endpoint seen across the runs. Prints PASS/FAIL and exits non-zero on any FAIL.
"""
import glob
import json
import os
import sys

reports = sys.argv[1]
combos = sys.argv[2].split(",")
fails = []


def newest_json(folder):
    files = glob.glob(os.path.join(folder, "*.json"))
    return max(files, key=os.path.getmtime) if files else None


all_endpoint_names = set()
for combo in combos:
    folder = os.path.join(reports, combo)
    j = newest_json(folder)
    if not j:
        fails.append(f"{combo}: no JSON report in {folder}")
        continue
    with open(j) as f:
        d = json.load(f)
    if d.get("combo") != combo:
        fails.append(f"{combo}: json combo field is {d.get('combo')!r}")
    eps = d.get("endpoints", [])
    if not eps:
        fails.append(f"{combo}: endpoints table is empty")
    else:
        missing_p95 = [e["name"] for e in eps if "p95" not in e]
        if missing_p95:
            fails.append(f"{combo}: {len(missing_p95)} endpoints have no p95")
        all_endpoint_names.update(e["name"] for e in eps)
    reqs = (d.get("summary") or {}).get("requests", 0)
    if not reqs or reqs <= 0:
        fails.append(f"{combo}: summary.requests is {reqs}")
    print(f"  combo {combo}: {len(eps)} endpoints, {reqs} requests — "
          f"{'ok' if j else 'MISSING'}")

# consolidated report
cons = sorted(glob.glob(os.path.join(reports, "consolidated-*.md")))
if not cons:
    fails.append("no consolidated-*.md written")
else:
    text = open(cons[-1]).read()
    for combo in combos:
        if combo not in text:
            fails.append(f"consolidated report does not mention combo {combo}")
    if "Per-endpoint across combos" not in text:
        fails.append("consolidated report missing per-endpoint section")
    # every endpoint name should appear as a row
    missing_rows = [n for n in all_endpoint_names if f"| {n} |" not in text]
    if missing_rows:
        fails.append(f"consolidated report missing {len(missing_rows)} endpoint rows "
                     f"(e.g. {missing_rows[:3]})")
    print(f"  consolidated: {os.path.basename(cons[-1])}, "
          f"{len(all_endpoint_names)} endpoints across combos")

if fails:
    print("FAIL")
    for x in fails:
        print(f"    - {x}")
    sys.exit(1)
print("PASS")
