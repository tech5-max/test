"""Builds the Locust report (same layout as the k6 service):
   reports/<mode>[-ai]-<time>.md and .json, plus the full result in the terminal."""
import json
import os
import time

from config import level_of
from stats import CLASSES, CRASH, LABELS, summarize

LEVELS = {
    "healthy": ("🟢", "Easily serves"),
    "stressed": ("🟡", "Under stress but survives"),
    "critical": ("🔴", "Critical: server crash"),
}
LAT_COLS = [("avg", "Avg"), ("p(85)", "p85"), ("p(90)", "p90"), ("p(95)", "p95"), ("p(99)", "p99"), ("p(99.9)", "p99.9"), ("max", "Max")]


def ms(v):
    if v is None:
        return "–"
    return f"{v / 1000:.2f}s" if v >= 1000 else f"{round(v)}ms"


def pct(v):
    return "–" if v is None else f"{v * 100:.2f}%"


def num(v):
    return "–" if v is None else f"{round(v):,}"


def rps(v):
    return f"{round(v)} req/s" if v >= 10 else f"{v:.1f} req/s"


def load(v):
    return f"{rps(v)} ({num(v * 60)} req/min)"


def breakdown(counts, classes=None):
    classes = classes or [c for c in CLASSES if c != "ok"]
    return ", ".join(f"{num(counts[c])} {LABELS[c]}" for c in classes if counts.get(c))


def build_report(cfg, steps_plan, stats, run_seconds, started_at, endpoint_count, workers):
    host = cfg["base_url"].split("://", 1)[1]
    dur_text = f"{int(run_seconds // 60)}m {int(round(run_seconds % 60))}s"

    # ---- steps ----
    steps = []
    for st in steps_plan:
        slot = stats.steps.get(st["index"])
        if not slot:
            continue
        s = {**summarize(slot), **st}
        if s["reqs"] == 0:
            continue
        window = max(0.0, min(st["end"], run_seconds) - st["start"])
        planned = st["requests"] * window / (st["end"] - st["start"])
        s["achieved_rps"] = s["reqs"] / window if window else 0
        s["complete"] = run_seconds >= st["end"]
        # Limited = the test machine failed, or users were at MAX_VUS and still
        # couldn't send the planned load (same rule as k6).
        out_of_users = s["users"] >= cfg["max_users"] * 0.95 and s["started"] < planned * 0.95
        s["generator_limited"] = planned >= 20 and (s["client_limit_rate"] > 0.01 or s["cpu_warn"] or out_of_users)
        s["level"] = level_of(cfg, s["err_rate"], s["latency"]["p(95)"] or 0, s["hard_rate"])
        steps.append(s)

    # ---- totals ----
    totals = {c: 0 for c in CLASSES}
    hist_all = {"counts": {}, "hist": {}, "sum": 0.0, "max": 0.0, "started": 0, "users": 0, "cpu_warn": False}
    from collections import Counter
    hist_all["counts"], hist_all["hist"] = Counter(), Counter()
    for slot in stats.steps.values():
        hist_all["counts"].update(slot["counts"])
        hist_all["hist"].update(slot["hist"])
        hist_all["sum"] += slot["sum"]
        hist_all["max"] = max(hist_all["max"], slot["max"])
        hist_all["users"] = max(hist_all["users"], slot["users"])
    overall = summarize(hist_all)
    totals = overall["counts"]
    total_reqs = overall["reqs"]
    total_crash = overall["crash"]
    err_rate = overall["err_rate"]
    peak_users = overall["users"]
    lat = overall["latency"]

    endpoints = sorted(({"name": k, **summarize(v)} for k, v in stats.endpoints.items()),
                       key=lambda e: -(e["latency"]["p(95)"] or 0))

    # ---- verdict ----
    stages, load_result, stop_reason = None, None, ""
    if cfg["mode"] == "stress":
        first_crit = next((s for s in steps if s["level"] == "critical"), None)
        if first_crit:
            steps = [s for s in steps if s["index"] <= first_crit["index"]]
        limited_at = next((s for s in steps if s["generator_limited"]), None)
        before = [s for s in steps if (not first_crit or s["index"] < first_crit["index"]) and (not limited_at or s["index"] < limited_at["index"])]
        first_not_healthy = next((s for s in before if s["level"] != "healthy"), None)
        healthy = [s for s in before if not first_not_healthy or s["index"] < first_not_healthy["index"]]
        stressed = [s for s in before if first_not_healthy and s["index"] >= first_not_healthy["index"]]
        last = steps[-1] if steps else None
        stages = [
            {"level": "healthy",
             "load": f"Up to {load(healthy[-1]['rps'])}" if healthy else ("Not measured (test machine was the limit)" if limited_at and steps and limited_at["index"] == steps[0]["index"] else "Not reached"),
             "detail": (f"p95 ≤ {ms(max(s['latency']['p(95)'] or 0 for s in healthy))}, errors ≤ {pct(max(s['err_rate'] for s in healthy))}, up to {num(max(s['users'] for s in healthy))} users") if healthy else ""},
            {"level": "stressed",
             "load": (f"{load(stressed[0]['rps'])} – {load(stressed[-1]['rps'])}" if stressed else ("None: it went straight from comfortable to crashing" if first_crit else "Not reached")),
             "detail": (f"Still serving: p95 up to {ms(max(s['latency']['p(95)'] or 0 for s in stressed))}, errors up to {pct(max(s['err_rate'] for s in stressed))}, completed up to {rps(max(s['achieved_rps'] for s in stressed))}, up to {num(max(s['users'] for s in stressed))} users") if stressed else ""},
            {"level": "critical",
             "load": f"At {load(first_crit['rps'])}" if first_crit else f"Not reached up to {load(last['rps'] if last else 0)}",
             "detail": (f"The server is failing: {pct(first_crit['hard_rate'])} of requests got {breakdown(first_crit['counts'], CRASH)}; p95 {ms(first_crit['latency']['p(95)'])}, {num(first_crit['users'])} users")
             if first_crit else "No server failures at the highest load tested. Raise STRESS_MAX_DURATION or STRESS_STEP_REQUESTS to push further."},
        ]
        max_surv = before[-1] if before else None
        peak = max([s["achieved_rps"] for s in before] or [0])
        if first_crit:
            stop_reason = f"Stopped automatically at step {first_crit['index']}: the server started failing ({breakdown(first_crit['counts'], CRASH)})."
        elif len(steps) >= len(steps_plan):
            stop_reason = f"Reached STRESS_MAX_DURATION ({dur_text}) without the server failing."
        else:
            stop_reason = f"Run ended at {dur_text} before reaching STRESS_MAX_DURATION (interrupted)."
        if limited_at and (not first_crit or limited_at["index"] <= first_crit["index"]):
            stop_reason += (f" ⚠ From step {limited_at['index']} ({load(limited_at['rps'])}) the test machine couldn't send the full load, "
                            "so the server's limit above that is unknown: use a bigger load machine.")
        result = {"stages": stages, "max_survivable": load(max_surv["rps"]) if max_surv else "–",
                  "peak_throughput": load(peak) if peak else "–", "stop_reason": stop_reason}
    else:
        s = steps[0] if steps else None
        load_result = ({**s, "text": f"{LEVELS[s['level']][1]} at {load(cfg['load_rpm'] / 60)}"} if s else
                       {"level": "critical", "text": "No requests completed", "latency": {}, "err_rate": 1, "achieved_rps": 0, "reqs": 0, "users": 0})
        result = {"level": load_result["level"], "text": load_result["text"]}

    limited = next((s for s in steps if s["generator_limited"]), None)
    notes = []
    if limited:
        notes.append(f"⚠ Load generator limit: from step {limited['index']} ({load(limited['rps'])}) the test machine couldn't send the full load "
                     f"({num(totals['client_limit'])} requests failed on the test machine; CPU overloaded or users capped at MAX_VUS {num(cfg['max_users'])}). "
                     "Numbers from there describe the test machine, not the server. Use more CPU cores or a bigger machine.")
    elif totals["client_limit"]:
        notes.append(f"{num(totals['client_limit'])} requests failed on the test machine (sockets/files/DNS), not counted against the server.")
    if totals["rate_limited"]:
        notes.append(f"{num(totals['rate_limited'])} requests were rate-limited (429): the server's rate limiter was on, so this is not real capacity. "
                     "Set RATE_LIMIT_ENABLED=false on the server.")
    notes.append("Users pace themselves to the target rate; when responses get slower, users can't keep up, "
                 "so 'Completed' below 'Sent' with no failures means the server is queueing requests.")
    notes.append("Requests still in flight when the test stopped are not counted.")
    notes.append("Response times are measured from the test machine, so they include network time to the server.")

    rules = (f"🟢 server errors ≤ {pct(cfg['healthy_error_rate'])} and p95 ≤ {ms(cfg['healthy_p95_ms'])} · "
             f"🔴 server failing (5xx or no response) on > {pct(cfg['critical_error_rate'])} of requests · 🟡 in between (slowness alone is never 🔴)")
    head = ["Step", "Time", "Sent", "Completed", "Users", "Requests", "Success", "Failures", "p95", "p99", "Stage"]

    def row(s):
        icon, label = LEVELS[s["level"]]
        return [f"{s['index']}{'' if s['complete'] else '*'}", f"{int(s['start'])}–{int(s['end'])}s", rps(s["rps"]), rps(s["achieved_rps"]),
                num(s["users"]), num(s["reqs"]), pct(1 - s["err_rate"]), num(s["crash"]), ms(s["latency"]["p(95)"]), ms(s["latency"]["p(99)"]),
                f"{icon} {label}{' ⚠ generator-limited' if s['generator_limited'] else ''}"]

    # ---- markdown ----
    L = [f"# Load Test Report: {host}", "",
         f"**Date:** {time.strftime('%Y-%m-%d %H:%M', time.gmtime(started_at))} UTC · **Tool:** Locust ({workers} worker process{'es' if workers != 1 else ''}) · "
         f"**Mode:** {cfg['mode']} · **Duration:** {dur_text} · **AI APIs:** {'included' if cfg['include_ai'] else 'excluded'} · **Endpoints:** {endpoint_count}", "",
         "## Result", ""]
    if stages:
        L += ["| Stage | Load | Detail |", "|---|---|---|"]
        L += [f"| {LEVELS[s['level']][0]} **{LEVELS[s['level']][1]}** | {s['load']} | {s['detail']} |" for s in stages]
        L += ["", f"**Maximum load it survives:** {result['max_survivable']} sent · **Peak throughput:** {result['peak_throughput']} completed.", "", stop_reason, ""]
    else:
        lr = load_result
        L += [f"{LEVELS[lr['level']][0]} **{lr['text']}** for {dur_text}: p95 {ms(lr['latency'].get('p(95)'))}, p99 {ms(lr['latency'].get('p(99)'))}, "
              f"{pct(lr['err_rate'])} errors, {rps(lr['achieved_rps'])} completed.", ""]
    L += [f"<sub>{rules}</sub>", "", "## Summary", "",
          f"| Requests | Success | Server failures | Peak users | {' | '.join(c[1] for c in LAT_COLS)} |",
          "|" + "---|" * (4 + len(LAT_COLS)),
          f"| {num(total_reqs)} | {pct(1 - err_rate)} | {num(total_crash)} | {num(peak_users)} | {' | '.join(ms(lat[c[0]]) for c in LAT_COLS)} |", ""]
    bd = breakdown(totals)
    L += [f"**Failures by type:** {bd}." if bd else "**Failures:** none.", ""]
    if stages:
        L += ["## Steps", "", f"| {' | '.join(head)} |", "|" + "---|" * len(head)]
        L += [f"| {' | '.join(row(s))} |" for s in steps]
        L += ["", "Sent = load the test sent · Completed = what the server actually answered per second · Failures = 5xx or no response · * = step cut short.", ""]
    else:
        lr = load_result
        L += ["## Load", "", "| Target | Completed | Requests | Peak users |", "|---|---|---|---|",
              f"| {load(cfg['load_rpm'] / 60)} | {rps(lr['achieved_rps'])} | {num(lr['reqs'])} | {num(lr['users'])} |", ""]
    L += ["## Slowest endpoints", "", "| Endpoint | Requests | p95 | p99 | Failures |", "|---|---|---|---|---|"]
    L += [f"| {e['name']} | {num(e['reqs'])} | {ms(e['latency']['p(95)'])} | {ms(e['latency']['p(99)'])} | {num(e['crash'])} |" for e in endpoints[:5]]
    L += ["", "## Notes", ""] + [f"- {n}" for n in notes] + [""]
    md = "\n".join(L)

    # ---- terminal ----
    T = ["", f"════ Load Test Report: {host} · Locust · {cfg['mode']} · AI {'on' if cfg['include_ai'] else 'off'} · {dur_text} ════"]
    if stages:
        for s in stages:
            T += [f"  {LEVELS[s['level']][0]} {LEVELS[s['level']][1]:<27} {s['load']}", f"     {s['detail']}" if s["detail"] else ""]
        T += [f"  Max load it survives: {result['max_survivable']} sent · peak throughput {result['peak_throughput']} completed", f"  {stop_reason}"]
    else:
        lr = load_result
        T += [f"  {LEVELS[lr['level']][0]} {lr['text']}: p95 {ms(lr['latency'].get('p(95)'))}, {pct(lr['err_rate'])} errors, {rps(lr['achieved_rps'])} completed"]
    T += [f"  Requests {num(total_reqs)} · success {pct(1 - err_rate)} · " + " · ".join(f"{c[1]} {ms(lat[c[0]])}" for c in LAT_COLS) + f" · peak users {num(peak_users)}",
          f"  Failures: {bd or 'none'}"]
    if stages and steps:
        w = [5, 9, 11, 11, 7, 9, 9, 9, 8, 8]
        T += ["", "  " + " ".join(h.ljust(w[i]) if i < 2 else h.rjust(w[i]) for i, h in enumerate(head[:10])) + "  Stage"]
        for s in steps:
            r = row(s)
            T.append("  " + " ".join(c.ljust(w[i]) if i < 2 else c.rjust(w[i]) for i, c in enumerate(r[:10])) + "  " + r[10])
    T += [f"  {n}" for n in notes if n.startswith("⚠") or "429" in n]

    os.makedirs("reports", exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime(started_at))
    base = f"reports/{cfg['mode']}{'-ai' if cfg['include_ai'] else ''}-{stamp}"
    with open(f"{base}.md", "w") as f:
        f.write(md)
    with open(f"{base}.json", "w") as f:
        json.dump({"tool": "locust", "settings": cfg, "startedAt": stamp, "runSeconds": run_seconds, "result": result,
                   "totals": {"requests": total_reqs, "serverFailures": total_crash, "errRate": err_rate, "peakUsers": peak_users, **totals},
                   "latency": lat, "steps": steps, "endpoints": endpoints}, f, indent=2, default=str)
    T += ["", f"  Report: {base}.md  (also .json)", ""]
    print("\n".join(t for t in T if t is not None), flush=True)
    return base
