#!/usr/bin/env python3
"""Merge the per-combo eon load-test reports into one combined Markdown report.

Reads the newest <mode>*.json in each reports/<combo>/ folder and writes
reports/consolidated-<time>.md with:
  1. a combo summary table (one row per combo), and
  2. a per-endpoint table with every endpoint side-by-side across the combos
     (each cell = p95 / error%), so you can see the cache + multi-process effect
     per API at a glance.

Pure stdlib. Run via ./consolidate.sh (Docker) or `python3 consolidate.py`.
Optional args: explicit combo JSON paths (else it auto-discovers the folders).
"""
import glob
import json
import os
import sys
import time

# The 4 load-test conditions, in display order. Auto-discovery includes ONLY these
# (stray folders from ad-hoc runs are ignored); pass explicit paths to override.
COMBO_ORDER = [
    "single-cache-on",
    "single-cache-off",
    "multi-cache-on",
    "multi-cache-off",
]


def ms(v):
    if v is None:
        return "–"
    return f"{v/1000:.2f}s" if v >= 1000 else f"{round(v)}ms"


def pct(v):
    return "–" if v is None else f"{v*100:.2f}%"


def rps(v):
    if v is None:
        return "–"
    return f"{round(v)}/s" if v >= 10 else f"{v:.1f}/s"


def newest_json(folder):
    files = glob.glob(os.path.join(folder, "*.json"))
    return max(files, key=os.path.getmtime) if files else None


def discover(reports_dir):
    """Return [(combo, path)] for each combo folder's newest json, ordered."""
    found = {}
    for entry in sorted(os.listdir(reports_dir)):
        folder = os.path.join(reports_dir, entry)
        if not os.path.isdir(folder):
            continue
        j = newest_json(folder)
        if j:
            found[entry] = j
    # Only the 4 canonical conditions, in order; ignore any stray folders.
    return [(c, found[c]) for c in COMBO_ORDER if c in found]


def load(pairs):
    runs = []
    for combo, path in pairs:
        with open(path) as f:
            data = json.load(f)
        runs.append((combo, data))
    return runs


def build_md(runs):
    stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    L = [
        "# eon Load Test — consolidated report",
        "",
        f"**Date:** {stamp} UTC · **Combos:** {len(runs)} · **Tool:** k6",
        "",
        "Each combo is one run against eon in a given state (cache on/off, single vs "
        "multi process). The server state is set by restarting eon; this report only "
        "merges the saved runs.",
        "",
        "## Combos",
        "",
        "| Combo | Mode | Requests | Success | Peak throughput | p95 | p99 | Server failures | Result |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for combo, d in runs:
        s = d.get("summary", {})
        L.append(
            f"| `{combo}` | {d.get('mode','–')} | {s.get('requests','–'):,} | "
            f"{pct(s.get('successRate'))} | {rps(s.get('peakThroughput'))} | "
            f"{ms(s.get('p95'))} | {ms(s.get('p99'))} | {s.get('serverFailures','–')} | "
            f"{s.get('verdict','–')} |"
        )

    # ---- per-endpoint, side by side across combos ----
    # Union of endpoint names, keeping a stable service label.
    names = []
    svc_of = {}
    for _, d in runs:
        for e in d.get("endpoints", []):
            if e["name"] not in svc_of:
                svc_of[e["name"]] = e.get("svc", "—")
                names.append(e["name"])

    per = {c: {e["name"]: e for e in d.get("endpoints", [])} for c, d in runs}
    combo_names = [c for c, _ in runs]

    L += [
        "",
        "## Per-endpoint across combos",
        "",
        "Each cell is **p95 / error%** for that endpoint in that combo.",
        "",
        "| Endpoint | Service | " + " | ".join(f"`{c}`" for c in combo_names) + " |",
        "|---|---|" + "---|" * len(combo_names),
    ]
    for name in names:
        cells = []
        for c in combo_names:
            e = per[c].get(name)
            if e is None:
                cells.append("–")
            else:
                cells.append(f"{ms(e.get('p95'))} / {pct(e.get('errRate'))}")
        L.append(f"| {name} | {svc_of[name]} | " + " | ".join(cells) + " |")

    L += [
        "",
        "## Notes",
        "",
        "- **p95 / error%** per cell; lower p95 and 0% error is better.",
        "- Cache-off combos exclude the AI/paid-feed routes and make the rate-limit "
        "store inert — compare them to cache-on on the shared read/compute endpoints.",
        "- A combo missing an endpoint (–) means that run didn't include it "
        "(e.g. AI off, or express-guardian without a cookie).",
        "",
    ]
    return "\n".join(L)


def main():
    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    args = sys.argv[1:]
    if args:
        # explicit json paths; combo name = parent folder
        pairs = [(os.path.basename(os.path.dirname(p)) or p, p) for p in args]
    else:
        if not os.path.isdir(reports_dir):
            print(f"✗ No reports directory at {reports_dir}", file=sys.stderr)
            sys.exit(1)
        pairs = discover(reports_dir)
    if not pairs:
        print("✗ No combo reports found. Run ./run.sh for each combo first.", file=sys.stderr)
        sys.exit(1)

    runs = load(pairs)
    md = build_md(runs)
    os.makedirs(reports_dir, exist_ok=True)
    out = os.path.join(reports_dir, f"consolidated-{time.strftime('%Y-%m-%dT%H-%M-%SZ', time.gmtime())}.md")
    with open(out, "w") as f:
        f.write(md)
    print(md)
    print(f"\n  Consolidated report: {out}\n")


if __name__ == "__main__":
    main()
