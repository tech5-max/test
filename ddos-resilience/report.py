#!/usr/bin/env python3
"""Writes the DDoS-resilience report (Markdown + JSON) and prints the full
result to the terminal. Also: `python3 report.py --combine reports/*.json`
prints a phase-1-vs-phase-2 comparison and writes a combined Markdown file."""
import glob
import json
import os
import sys
import time


def ms(v):
    if v is None:
        return "–"
    return f"{v/1000:.2f}s" if v >= 1000 else f"{round(v)}ms"


def n(v):
    return "–" if v is None else f"{round(v):,}"


def p95_of(samples, t):
    for s in samples:
        if s["t"] == t:
            return s.get("p95")
    return None


def verdict(r):
    """(emoji, short, one-line for the write-up)."""
    if r["time_to_block"] is not None:
        return ("🔴", "Server blocked us",
                f"The server stopped answering after {n(r['requests_before_block'])} requests "
                f"(~{r['time_to_block']:.0f}s): our connections were dropped (fail2ban / firewall). "
                f"{n(r['blocked_after'])} later requests got no response.")
    tot = r["total_requests"] or 1
    degraded = (r["totals"].get("slow", 0) > 0.05 * tot
                or any((s.get("p95") or 0) > r["degrade_p95_ms"] for s in r["samples"])
                or r["totals"]["server_5xx"] > 0.05 * tot)
    if degraded:
        return ("🟠", "Server degraded (not blocked)",
                f"The server kept answering but slowed badly / returned errors under {r['peak_rps']:.0f} req/s. "
                "No IP block was triggered — add fail2ban / rate limiting.")
    return ("🟢", "Server held up",
            f"The server served the whole flood (~{r['peak_rps']:.0f} req/s, {n(r['total_requests'])} requests) "
            "without blocking or failing. No block was triggered.")


def build_md(r):
    emoji, short, sentence = verdict(r)
    host = r["target"].split("://", 1)[1]
    dur = f"{int(r['duration'] // 60)}m {int(r['duration'] % 60)}s"
    t = r["totals"]
    L = [f"# DDoS Resilience Report: {host}", "",
         f"**Date:** {time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['started_at']))} UTC · "
         f"**Phase:** {r['phase']} · **Endpoint:** `{r['endpoint']}` · **Source:** one IP · "
         f"**Target rate:** {r['rate_target']:.0f} req/s · **Duration:** {dur}", "",
         "## Verdict", "", f"{emoji} **{short}.** {sentence}", "",
         "## Key numbers", "",
         "| Metric | Value |", "|---|---|",
         f"| Peak request rate reached | {r['peak_rps']:.0f} req/s |",
         f"| Total requests sent | {n(r['total_requests'])} |",
         f"| Requests the server accepted | {n(r['accepted'])} ({', '.join(f'{n(t[k])} {k}' for k in ('ok','rate_limited','server_5xx') if t[k])}) |",
         f"| Requests before we were blocked | {n(r['requests_before_block'])} |",
         f"| Time until blocked | {r['time_to_block']:.0f}s | " if r["time_to_block"] is not None else "| Time until blocked | not blocked |",
         f"| Requests dropped after block | {n(r['blocked_after'])} |",
         f"| Connection drops (blocked) total | {n(t['blocked'])} |", "",
         "## Timeline (per second)", "",
         "| Time | Sent | OK | 429 | Slow | Blocked | p95 |", "|---|---|---|---|---|---|---|"]
    for s in r["samples"]:
        sent = sum(s.get(k, 0) for k in ("ok", "rate_limited", "server_5xx", "slow", "blocked", "error"))
        L.append(f"| {s['t']}s | {sent} | {s['ok']} | {s['rate_limited']} | {s.get('slow',0)} | {s['blocked']} | {ms(s.get('p95'))} |")
    L += ["", "## What this means", "",
          "- **OK** = the server answered (2xx).",
          "- **429** = the server's own rate limiter rejected the request (first line of defense — the server stayed up).",
          "- **Slow** = the connection succeeded but the server was too slow to answer in time: alive but overloaded (not a firewall block).",
          "- **Blocked** = the TCP connection was refused, reset, or could not be made: the firewall (fail2ban) dropped us.",
          "- Measured from one machine over its single real IP; fail2ban bans by that real IP, so a single IP is the correct way to prove the block.", ""]
    return "\n".join(L)


def build_terminal(r):
    emoji, short, sentence = verdict(r)
    host = r["target"].split("://", 1)[1]
    t = r["totals"]
    T = ["", f"════ DDoS Resilience Report: {host} · phase {r['phase']} ════",
         f"  {emoji} {short}", f"  {sentence}",
         f"  Peak {r['peak_rps']:.0f} req/s · sent {n(r['total_requests'])} · accepted {n(r['accepted'])}"
         f" · ok {n(t['ok'])} · 429 {n(t['rate_limited'])} · slow {n(t.get('slow',0))} · blocked {n(t['blocked'])}"]
    if r["time_to_block"] is not None:
        T.append(f"  ⛔ Blocked after {n(r['requests_before_block'])} requests (~{r['time_to_block']:.0f}s); {n(r['blocked_after'])} requests dropped afterwards.")
    return "\n".join(T)


def write_report(r):
    os.makedirs("reports", exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime(r["started_at"]))
    base = f"reports/ddos-{r['phase']}-{stamp}"
    with open(f"{base}.md", "w") as f:
        f.write(build_md(r))
    with open(f"{base}.json", "w") as f:
        json.dump(r, f, indent=2)
    print(build_terminal(r), flush=True)
    print(f"\n  Report: {base}.md  (also .json)\n", flush=True)


def combine(paths):
    runs = [json.load(open(p)) for p in paths]
    runs.sort(key=lambda r: r["started_at"])
    host = runs[0]["target"].split("://", 1)[1]
    L = [f"# DDoS Resilience: before vs after fail2ban — {host}", "",
         "| Phase | Peak req/s | Requests sent | Accepted | Blocked after | Time to block | Verdict |",
         "|---|---|---|---|---|---|---|"]
    for r in runs:
        emoji, short, _ = verdict(r)
        ttb = f"{r['time_to_block']:.0f}s" if r["time_to_block"] is not None else "not blocked"
        L.append(f"| {r['phase']} | {r['peak_rps']:.0f} | {n(r['total_requests'])} | {n(r['accepted'])} | "
                 f"{n(r['blocked_after'])} | {ttb} | {emoji} {short} |")
    out = "\n".join(L) + "\n"
    os.makedirs("reports", exist_ok=True)
    path = "reports/ddos-comparison.md"
    with open(path, "w") as f:
        f.write(out)
    print(out, flush=True)
    print(f"  Combined report: {path}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--combine":
        files = sys.argv[2:] or sorted(glob.glob("reports/ddos-*.json"))
        combine(files)
    else:
        print("Usage: report.py --combine [reports/*.json]")
