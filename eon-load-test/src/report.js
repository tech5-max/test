// Builds the reports from the k6 summary:
//   reports/<mode>[-ai]-<time>.md    short report (the one to read on a VPS)
//   reports/<mode>[-ai]-<time>.html  the same as a styled page with a chart
//   reports/<mode>[-ai]-<time>.json  full per-step and per-endpoint data
// and prints the full result to the terminal.
//
// Stages (per step):
//   🟢 Easily serves             server errors ≤ HEALTHY_ERROR_RATE and p95 ≤ HEALTHY_P95_MS
//   🟡 Under stress but survives  in between (slowness alone never makes it 🔴)
//   🔴 Critical: server crash     crash-type failures HTTP 500+ only > CRITICAL_ERROR_RATE
// Test-machine failures (client_limit, dropped requests) are kept out of the
// server's numbers and flagged as ⚠ generator-limited.
import { CONFIG, STEPS, levelOf } from "./config.js";
import { CLASSES, CRASH_CLASSES, CLASS_LABELS } from "./classes.js";
import { PUBLIC_ENDPOINTS, AI_ENDPOINTS, GUARDIAN_ENDPOINTS } from "./endpoints.js";

// name -> which service/group it belongs to, for the per-endpoint table.
const ENDPOINT_META = Object.fromEntries(
  [...PUBLIC_ENDPOINTS, ...AI_ENDPOINTS, ...GUARDIAN_ENDPOINTS].map((e) => [
    e.name,
    { svc: e.svc === "eg" ? "express-guardian" : "fast-dragon", group: e.group },
  ]),
);

const LEVELS = {
  healthy: { icon: "🟢", label: "Easily serves", color: "#16a34a" },
  stressed: { icon: "🟡", label: "Under stress but survives", color: "#d97706" },
  critical: { icon: "🔴", label: "Critical: server crash", color: "#dc2626" },
};

const ms = (v) => (v === undefined || v === null || Number.isNaN(v) ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);
const pct = (v) => (v === undefined || v === null || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(2)}%`);
// k6's JS engine has no locale support, so thousands separators by hand.
const int = (v) => (v === undefined || v === null || Number.isNaN(v) ? "–" : String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ","));
const rps = (v) => `${v >= 10 ? Math.round(v) : v.toFixed(1)} req/s`;
const load = (rpsValue) => `${rps(rpsValue)} (${int(rpsValue * 60)} req/min)`;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const pad = (s, n) => String(s).padEnd(n);
const lpad = (s, n) => String(s).padStart(n);

function sub(data, metric, tag, value) {
  return data.metrics[`${metric}{${tag}:${value}}`]?.values;
}

// Numbers for one step / endpoint. Server rates leave out client_limit
// requests: those failed on the test machine and say nothing about the server.
function slice(data, tag, value) {
  const dur = sub(data, "http_req_duration", tag, value) || {};
  const counts = Object.fromEntries(CLASSES.map((c) => [c, sub(data, `out_${c}`, tag, value)?.count ?? 0]));
  const reqs = CLASSES.reduce((sum, c) => sum + counts[c], 0);
  const serverReqs = reqs - counts.client_limit;
  const crash = CRASH_CLASSES.reduce((sum, c) => sum + counts[c], 0);
  return {
    reqs,
    counts,
    crash,
    errRate: serverReqs ? (serverReqs - counts.ok) / serverReqs : 0,
    hardErrRate: serverReqs ? crash / serverReqs : 0,
    clientLimitRate: reqs ? counts.client_limit / reqs : 0,
    dur,
    vus: sub(data, "active_vus", tag, value)?.max ?? 0,
    started: sub(data, "started", tag, value)?.count ?? 0,
  };
}

function breakdown(counts, classes = CLASSES.filter((c) => c !== "ok")) {
  return classes
    .filter((c) => counts[c])
    .map((c) => `${int(counts[c])} ${CLASS_LABELS[c]}`)
    .join(", ");
}

export function buildSummary(data) {
  const runSeconds = (data.state?.testRunDurationMs ?? 0) / 1000;
  const overall = data.metrics.http_req_duration?.values || {};
  const totals = Object.fromEntries(CLASSES.map((c) => [c, data.metrics[`out_${c}`]?.values?.count ?? 0]));
  const totalReqs = CLASSES.reduce((sum, c) => sum + totals[c], 0);
  const serverReqs = totalReqs - totals.client_limit;
  const totalCrash = CRASH_CLASSES.reduce((sum, c) => sum + totals[c], 0);
  const serverErrors = serverReqs - totals.ok;
  const errRate = serverReqs ? serverErrors / serverReqs : 0;
  const dropped = data.metrics.dropped_iterations?.values?.count ?? 0;
  const peakVus = data.metrics.active_vus?.values?.max ?? 0;

  // ---- steps ----
  // achievedRps = requests started in the step that completed, per second
  // (the server's real throughput). The generator is "limited" in a step only
  // when the test machine itself failed (client_limit errors) or k6 ran out of
  // users (MAX_VUS) and couldn't start the planned requests. Requests not
  // started because a hanging server kept every user busy are the server's doing.
  let steps = STEPS.map((st) => {
    const s = slice(data, "step", st.index);
    const windowSec = Math.max(0, Math.min(st.end, runSeconds) - st.start);
    const planned = (st.requests * windowSec) / (st.end - st.start);
    const outOfUsers = s.vus >= CONFIG.maxVus * 0.95 && s.started < planned * 0.95;
    const generatorLimited = planned >= 20 && (outOfUsers || s.clientLimitRate > 0.01);
    return { ...s, ...st, planned, generatorLimited, achievedRps: windowSec > 0 ? s.reqs / windowSec : 0, complete: runSeconds >= st.end };
  })
    .filter((s) => s.reqs > 0)
    .map((s) => ({ ...s, level: levelOf(s.errRate, s.dur["p(95)"] ?? 0, s.hardErrRate) }));

  // ---- endpoints ----
  const endpoints = Object.keys(data.metrics)
    .map((k) => /^out_ok\{name:(.+)\}$/.exec(k)?.[1])
    .filter(Boolean)
    .map((name) => ({ name, ...slice(data, "name", name) }))
    .filter((e) => e.reqs > 0)
    .sort((a, b) => (b.dur["p(95)"] ?? 0) - (a.dur["p(95)"] ?? 0));

  const startedAt = new Date(Date.now() - runSeconds * 1000);
  const host = CONFIG.baseUrl.replace(/^https?:\/\//, "");
  const durationText = `${Math.floor(runSeconds / 60)}m ${Math.round(runSeconds % 60)}s`;

  // ---- verdict ----
  let stages = null;
  let loadResult = null;
  let stopReason = "";
  if (CONFIG.mode === "stress") {
    const firstCritical = steps.find((s) => s.level === "critical");
    // Steps after the crash step were cut short by the stop (only their fastest
    // requests finished), so they would look healthier than they were.
    if (firstCritical) steps = steps.filter((s) => s.index <= firstCritical.index);
    // Steps where the test machine was the limit say nothing about the
    // server's capacity, so the 🟢 / 🟡 verdicts only use steps before the
    // first such step. (A crash in a limited step is still a real crash.)
    const limitedAt = steps.find((s) => s.generatorLimited);
    const beforeCrash = (firstCritical ? steps.filter((s) => s.index < firstCritical.index) : steps).filter(
      (s) => !limitedAt || s.index < limitedAt.index,
    );
    const firstNotHealthy = beforeCrash.find((s) => s.level !== "healthy");
    const healthy = beforeCrash.filter((s) => !firstNotHealthy || s.index < firstNotHealthy.index);
    const stressed = firstNotHealthy ? beforeCrash.filter((s) => s.index >= firstNotHealthy.index) : [];
    const lastStep = steps[steps.length - 1];
    const maxOf = (list, f) => Math.max(...list.map(f));

    stages = [
      {
        level: "healthy",
        load: healthy.length ? `Up to ${load(healthy[healthy.length - 1].rps)}` : limitedAt && limitedAt.index === steps[0].index ? "Not measured (test machine was the limit)" : "Not reached",
        detail: healthy.length
          ? `p95 ≤ ${ms(maxOf(healthy, (s) => s.dur["p(95)"] ?? 0))}, errors ≤ ${pct(maxOf(healthy, (s) => s.errRate))}, up to ${int(maxOf(healthy, (s) => s.vus))} parallel users`
          : `Even the first step (${load(STEPS[0].rps)}) was above the comfortable limits`,
      },
      {
        level: "stressed",
        load: stressed.length
          ? `${load(stressed[0].rps)} – ${load(stressed[stressed.length - 1].rps)}`
          : firstCritical
            ? "None: it went straight from comfortable to crashing"
            : "Not reached",
        detail: stressed.length
          ? `Still serving: p95 up to ${ms(maxOf(stressed, (s) => s.dur["p(95)"] ?? 0))}, errors up to ${pct(maxOf(stressed, (s) => s.errRate))}, completed up to ${rps(maxOf(stressed, (s) => s.achievedRps))}, up to ${int(maxOf(stressed, (s) => s.vus))} parallel users`
          : "",
      },
      {
        level: "critical",
        load: firstCritical ? `At ${load(firstCritical.rps)}` : `Not reached up to ${load(lastStep ? lastStep.rps : 0)}`,
        detail: firstCritical
          ? `The server is failing: ${pct(firstCritical.hardErrRate)} of requests got ${breakdown(firstCritical.counts, CRASH_CLASSES)}; p95 ${ms(firstCritical.dur["p(95)"])}, ${int(firstCritical.vus)} parallel users`
          : "No server failures at the highest load tested. Raise STRESS_MAX_DURATION or STRESS_STEP_REQUESTS to push further.",
      },
    ];
    const maxSurvivable = beforeCrash[beforeCrash.length - 1];
    const peakThroughput = Math.max(0, ...beforeCrash.map((s) => s.achievedRps));
    stopReason = firstCritical
      ? `Stopped automatically at step ${firstCritical.index}: the server started failing (${breakdown(firstCritical.counts, CRASH_CLASSES)}).`
      : steps.length >= STEPS.length
        ? `Reached STRESS_MAX_DURATION (${durationText}) without the server failing.`
        : `Run ended at ${durationText} before reaching STRESS_MAX_DURATION (interrupted).`;
    if (limitedAt && (!firstCritical || limitedAt.index <= firstCritical.index)) {
      stopReason += ` ⚠ From step ${limitedAt.index} (${load(limitedAt.rps)}) the test machine couldn't send the full load, so the server's limit above that is unknown: use a bigger load machine.`;
    }
    stages.maxSurvivable = maxSurvivable ? load(maxSurvivable.rps) : "–";
    stages.peakThroughput = peakThroughput ? load(peakThroughput) : "–";
  } else {
    const s = steps[0];
    loadResult = s
      ? { ...s, text: `${LEVELS[s.level].label} at ${load(CONFIG.loadRpm / 60)}` }
      : { level: "critical", text: "No requests completed", dur: {}, counts: {}, reqs: 0, errRate: 1, hardErrRate: 1, vus: 0, achievedRps: 0 };
  }

  const firstLimited = steps.find((s) => s.generatorLimited);
  const notes = [];
  if (firstLimited) {
    notes.push(
      `⚠ Load generator limit: from step ${firstLimited.index} (${load(firstLimited.rps)}) the test machine couldn't send the full load` +
        ` (${int(totals.client_limit)} requests failed on the test machine, ${int(dropped)} not sent, users at MAX_VUS ${int(CONFIG.maxVus)}). ` +
        `Numbers from there describe the test machine, not the server. Raise MAX_VUS, use a bigger machine, or raise the open-file limit.`,
    );
  } else if (dropped || totals.client_limit) {
    notes.push(
      `${int(dropped)} planned requests were not sent` +
        (totals.client_limit ? ` and ${int(totals.client_limit)} failed on the test machine` : "") +
        `: k6 had no free user at that moment, usually because slow or hanging responses kept them busy while new ones were added. ` +
        `This is the server's slowness showing, not a test-machine limit.`,
    );
  }
  if (totals.rate_limited) notes.push(`${int(totals.rate_limited)} requests were rate-limited (429): the server's rate limiter was on, so this is not real capacity. Set RATE_LIMIT_ENABLED=false on the server.`);
  notes.push("k6's own cancelled requests (in flight when the test stopped) are not counted.");
  notes.push("Response times are measured from the test machine, so they include network time to the server.");

  const latencyCols = [["avg", "Avg"], ["p(85)", "p85"], ["p(90)", "p90"], ["p(95)", "p95"], ["p(99)", "p99"], ["p(99.9)", "p99.9"], ["max", "Max"]];
  const rules = `🟢 server errors ≤ ${pct(CONFIG.healthyErrorRate)} and p95 ≤ ${ms(CONFIG.healthyP95Ms)} · 🔴 server returning HTTP 500+ on > ${pct(CONFIG.criticalErrorRate)} of requests · 🟡 in between (timeouts, resets and slowness are 🟡, never 🔴)`;
  const stepRow = (s) => [
    `${s.index}${s.complete ? "" : "*"}`,
    `${s.start}–${s.end}s`,
    rps(s.rps),
    rps(s.achievedRps),
    int(s.vus),
    int(s.reqs),
    pct(1 - s.errRate),
    int(s.crash),
    ms(s.dur["p(95)"]),
    ms(s.dur["p(99)"]),
    `${LEVELS[s.level].icon} ${LEVELS[s.level].label}${s.generatorLimited ? " ⚠ generator-limited" : ""}`,
  ];
  const stepHead = ["Step", "Time", "Sent", "Completed", "Users", "Requests", "Success", "Failures", "p95", "p99", "Stage"];

  // ---- markdown ----
  const L = [];
  L.push(`# eon Load Test Report: ${CONFIG.combo}`, "");
  L.push(`**Combo:** \`${CONFIG.combo}\` · **Date:** ${startedAt.toISOString().slice(0, 16).replace("T", " ")} UTC · **Tool:** k6 · **Mode:** ${CONFIG.mode} · **Duration:** ${durationText} · **AI APIs:** ${CONFIG.aiActive ? "included" : "excluded"} · **Endpoints:** ${endpoints.length}`, "");
  L.push(
    `**Service:** ${CONFIG.service}` +
      (CONFIG.wantFd ? ` · **fast-dragon:** ${host}` : "") +
      (CONFIG.wantEg && CONFIG.guardianBaseUrl ? ` · **express-guardian:** ${CONFIG.guardianBaseUrl.replace(/^https?:\/\//, "")}` : ""),
    "",
  );
  L.push("## Result", "");
  if (stages) {
    L.push("| Stage | Load | Detail |", "|---|---|---|");
    for (const st of stages) L.push(`| ${LEVELS[st.level].icon} **${LEVELS[st.level].label}** | ${st.load} | ${st.detail} |`);
    L.push("", `**Maximum load it survives:** ${stages.maxSurvivable} sent · **Peak throughput:** ${stages.peakThroughput} completed.`, "", stopReason, "");
  } else {
    L.push(`${LEVELS[loadResult.level].icon} **${loadResult.text}** for ${durationText}: p95 ${ms(loadResult.dur["p(95)"])}, p99 ${ms(loadResult.dur["p(99)"])}, ${pct(loadResult.errRate)} errors, ${rps(loadResult.achievedRps)} completed.`, "");
  }
  L.push(`<sub>${rules}</sub>`, "");
  L.push("## Summary", "");
  L.push(`| Requests | Success | Server failures | Peak users | ${latencyCols.map((c) => c[1]).join(" | ")} |`);
  L.push(`|${"---|".repeat(4 + latencyCols.length)}`);
  L.push(`| ${int(totalReqs)} | ${pct(1 - errRate)} | ${int(totalCrash)} | ${int(peakVus)} | ${latencyCols.map((c) => ms(overall[c[0]])).join(" | ")} |`, "");
  const bd = breakdown(totals);
  L.push(bd ? `**Failures by type:** ${bd}.` : "**Failures:** none.", "");
  if (stages) {
    L.push("## Steps", "");
    L.push(`| ${stepHead.join(" | ")} |`, `|${"---|".repeat(stepHead.length)}`);
    for (const s of steps) L.push(`| ${stepRow(s).join(" | ")} |`);
    L.push("", "Sent = load the test sent · Completed = what the server actually answered per second · Failures = HTTP 500+ (timeouts/resets do not count) · * = step cut short.", "");
  } else {
    L.push("## Load", "");
    L.push("| Target | Completed | Requests | Peak users |", "|---|---|---|---|");
    L.push(`| ${load(CONFIG.loadRpm / 60)} | ${rps(loadResult.achievedRps)} | ${int(loadResult.reqs)} | ${int(loadResult.vus)} |`, "");
  }
  // Full per-endpoint table (every endpoint, slowest first).
  L.push("## Per-endpoint results", "");
  L.push("| Endpoint | Service | Requests | Success | p90 | p95 | p99 | Failures |", "|---|---|---|---|---|---|---|---|");
  for (const e of endpoints) {
    const meta = ENDPOINT_META[e.name] || { svc: "—" };
    L.push(`| ${e.name} | ${meta.svc} | ${int(e.reqs)} | ${pct(1 - e.errRate)} | ${ms(e.dur["p(90)"])} | ${ms(e.dur["p(95)"])} | ${ms(e.dur["p(99)"])} | ${int(e.crash)} |`);
  }
  L.push("", "## Notes", "", ...notes.map((n) => `- ${n}`), "");
  const md = L.join("\n");

  // ---- terminal (full result, for servers without a browser) ----
  const T = [];
  T.push("", `════ Load Test Report: ${host} · k6 · ${CONFIG.mode} · AI ${CONFIG.aiActive ? "on" : "off"} · ${durationText} ════`);
  if (stages) {
    for (const st of stages) T.push(`  ${LEVELS[st.level].icon} ${pad(LEVELS[st.level].label, 27)} ${st.load}`, `     ${st.detail}`);
    T.push(`  Max load it survives: ${stages.maxSurvivable} sent · peak throughput ${stages.peakThroughput} completed`, `  ${stopReason}`);
  } else {
    T.push(`  ${LEVELS[loadResult.level].icon} ${loadResult.text}: p95 ${ms(loadResult.dur["p(95)"])}, ${pct(loadResult.errRate)} errors, ${rps(loadResult.achievedRps)} completed`);
  }
  T.push(`  Requests ${int(totalReqs)} · success ${pct(1 - errRate)} · ${latencyCols.map((c) => `${c[1]} ${ms(overall[c[0]])}`).join(" · ")} · peak users ${int(peakVus)}`);
  T.push(`  Failures: ${bd || "none"}`);
  if (stages && steps.length) {
    const w = [5, 9, 11, 11, 7, 9, 9, 9, 8, 8];
    T.push("", "  " + stepHead.slice(0, 10).map((h, i) => (i < 2 ? pad(h, w[i]) : lpad(h, w[i]))).join(" ") + "  Stage");
    for (const s of steps) {
      const r = stepRow(s);
      T.push("  " + r.slice(0, 10).map((c, i) => (i < 2 ? pad(c, w[i]) : lpad(c, w[i]))).join(" ") + "  " + r[10]);
    }
  }
  for (const n of notes.filter((x) => x.startsWith("⚠") || x.includes("429"))) T.push(`  ${n}`);

  // One sub-folder per combo so the 4 runs stay separate; consolidate reads them.
  const file = `reports/${CONFIG.combo}/${CONFIG.mode}${CONFIG.aiActive ? "-ai" : ""}-${startedAt.toISOString().replace(/[:.]/g, "-")}`;
  T.push("", `  Report: ${file}.md  (also .html, .json)`, "");

  // ---- json ----
  const json = {
    tool: "k6",
    combo: CONFIG.combo,
    mode: CONFIG.mode,
    includeAi: CONFIG.includeAi,
    settings: CONFIG,
    startedAt: startedAt.toISOString(),
    runSeconds,
    result: stages ? { stages, maxSurvivable: stages.maxSurvivable, peakThroughput: stages.peakThroughput, stopReason } : { level: loadResult.level, text: loadResult.text },
    // Top-line numbers consolidate.js reads per combo.
    summary: {
      requests: totalReqs,
      successRate: 1 - errRate,
      errRate,
      serverFailures: totalCrash,
      peakVus,
      peakThroughput: stages ? Math.max(0, ...steps.map((s) => s.achievedRps)) : loadResult.achievedRps,
      p95: overall["p(95)"] ?? null,
      p99: overall["p(99)"] ?? null,
      verdict: stages ? stages.maxSurvivable : (loadResult.text || ""),
    },
    totals: { requests: totalReqs, serverFailures: totalCrash, errRate, peakVus, dropped, ...totals },
    latency: overall,
    steps: steps.map(({ dur, ...s }) => ({ ...s, latency: dur })),
    endpoints: endpoints.map(({ dur, ...e }) => ({
      ...e,
      svc: (ENDPOINT_META[e.name] || {}).svc || "—",
      p95: dur["p(95)"] ?? null,
      p99: dur["p(99)"] ?? null,
      latency: dur,
    })),
  };

  const html = renderHtml({ host, startedAt, durationText, endpoints, steps, stages, stopReason, loadResult, totals, totalReqs, totalCrash, errRate, peakVus, overall, latencyCols, notes, rules, stepHead, stepRow, bd });
  return { stdout: T.join("\n"), [`${file}.md`]: md, [`${file}.html`]: html, [`${file}.json`]: JSON.stringify(json, null, 2) };
}

// ---------------------------------------------------------------------------
// HTML report
// ---------------------------------------------------------------------------

function chart(steps) {
  if (!steps.length) return "";
  const W = 760, H = 240, padL = 48, padR = 56, padT = 16, padB = 32;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxRps = Math.max(...steps.map((s) => s.rps)) || 1;
  const maxP95 = Math.max(...steps.map((s) => s.dur["p(95)"] ?? 0), CONFIG.healthyP95Ms) || 1;
  const bw = plotW / steps.length;
  const x = (i) => padL + i * bw;
  const yR = (v) => padT + plotH - (v / maxRps) * plotH;
  const yP = (v) => padT + plotH - (Math.min(v, maxP95) / maxP95) * plotH;
  const bars = steps
    .map((s, i) => `<rect x="${(x(i) + bw * 0.15).toFixed(1)}" y="${yR(s.rps).toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${(padT + plotH - yR(s.rps)).toFixed(1)}" fill="${LEVELS[s.level].color}" opacity="0.85"><title>Step ${s.index}: ${rps(s.rps)} sent, ${rps(s.achievedRps)} completed, p95 ${ms(s.dur["p(95)"])}</title></rect>` +
      `<rect x="${(x(i) + bw * 0.35).toFixed(1)}" y="${yR(s.achievedRps).toFixed(1)}" width="${(bw * 0.3).toFixed(1)}" height="${(padT + plotH - yR(s.achievedRps)).toFixed(1)}" fill="#111827" opacity="0.35"/>`)
    .join("");
  const line = steps.map((s, i) => `${(x(i) + bw / 2).toFixed(1)},${yP(s.dur["p(95)"] ?? 0).toFixed(1)}`).join(" ");
  const every = Math.max(1, Math.ceil(steps.length / 12));
  const labels = steps.map((s, i) => (i % every === 0 ? `<text x="${(x(i) + bw / 2).toFixed(1)}" y="${H - 10}" class="ax" text-anchor="middle">${Math.round(s.rps)}</text>` : "")).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Load per step and p95 response time">
  <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" class="grid"/>${bars}
  <polyline points="${line}" fill="none" stroke="#2563eb" stroke-width="2"/>${labels}
  <text x="${padL - 6}" y="${padT + 8}" class="ax" text-anchor="end">${Math.round(maxRps)}</text>
  <text x="${W - padR + 6}" y="${padT + 8}" class="ax">${ms(maxP95)}</text>
  <text x="${padL - 6}" y="${padT + plotH}" class="ax" text-anchor="end">0</text>
  <text x="${W / 2}" y="${H}" class="ax" text-anchor="middle">req/s sent per step</text>
</svg>
<p class="legend"><span style="color:#16a34a">■</span> Easily serves <span style="color:#d97706">■</span> Under stress <span style="color:#dc2626">■</span> Critical · dark inner bar = completed req/s · <span style="color:#2563eb">━</span> p95 (right axis)</p>`;
}

function renderHtml(r) {
  const table = (head, rows) => `<div class="tbl"><table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  let result;
  if (r.stages) {
    result = `<div class="cards">${r.stages.map((st) => `<div class="card" style="border-top-color:${LEVELS[st.level].color}"><div class="k">${LEVELS[st.level].icon} ${esc(LEVELS[st.level].label)}</div><div class="v">${esc(st.load)}</div><div class="d">${esc(st.detail)}</div></div>`).join("")}</div>
      <p class="lead"><b>Maximum load it survives:</b> ${esc(r.stages.maxSurvivable)} sent · <b>Peak throughput:</b> ${esc(r.stages.peakThroughput)} completed. ${esc(r.stopReason)}</p>
      <h2>Load vs response time</h2>${chart(r.steps)}
      <h2>Steps</h2>${table(r.stepHead, r.steps.map((s) => r.stepRow(s).map(esc)))}`;
  } else {
    const s = r.loadResult;
    result = `<div class="cards"><div class="card wide" style="border-top-color:${LEVELS[s.level].color}"><div class="k">${LEVELS[s.level].icon} ${esc(LEVELS[s.level].label)}</div><div class="v">${esc(load(CONFIG.loadRpm / 60))} for ${esc(r.durationText)}</div><div class="d">p95 ${ms(s.dur["p(95)"])}, ${pct(s.errRate)} errors, ${rps(s.achievedRps)} completed</div></div></div>`;
  }
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Load test: ${esc(r.host)}</title>
<style>
:root{--bg:#fff;--fg:#111827;--mute:#6b7280;--line:#e5e7eb;--card:#f9fafb}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f17;--fg:#e5e7eb;--mute:#9ca3af;--line:#1f2937;--card:#111827}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.meta{color:var(--mute);margin-bottom:20px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-top:4px solid;border-radius:8px;padding:14px}
.card .k{font-weight:600}.card .v{font-size:18px;font-weight:700;margin:6px 0}.card .d{color:var(--mute);font-size:13px}
.lead{margin:14px 0 0}.tbl{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--mute);font-weight:600}
.chart{width:100%;height:auto}.chart .ax{fill:var(--mute);font-size:11px}.chart .grid{stroke:var(--line)}.legend,.rules,.notes{color:var(--mute);font-size:12px}
</style></head><body><main>
<h1>Load Test Report: ${esc(r.host)}</h1>
<div class="meta">${esc(r.startedAt.toISOString().slice(0, 16).replace("T", " "))} UTC · k6 · ${esc(CONFIG.mode)} mode · ${esc(r.durationText)} · AI APIs ${CONFIG.aiActive ? "included" : "excluded"} · ${r.endpoints.length} endpoints</div>
${result}
<p class="rules">${esc(r.rules)}</p>
<h2>Summary</h2>${table(["Requests", "Success", "Server failures", "Peak users", ...r.latencyCols.map((c) => c[1])], [[int(r.totalReqs), pct(1 - r.errRate), int(r.totalCrash), int(r.peakVus), ...r.latencyCols.map((c) => ms(r.overall[c[0]]))]])}
<p class="notes">Failures by type: ${esc(r.bd || "none")}.</p>
<h2>Slowest endpoints</h2>${table(["Endpoint", "Requests", "p95", "p99", "Failures"], r.endpoints.slice(0, 5).map((e) => [esc(e.name), int(e.reqs), ms(e.dur["p(95)"]), ms(e.dur["p(99)"]), int(e.crash)]))}
<h2>Notes</h2><ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
</main></body></html>`;
}
