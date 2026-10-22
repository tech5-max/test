// k6 load test for the fast-dragon public preview GET APIs (logged out).
//   load:   fixed rate for DURATION
//   stress: load grows every STRESS_STEP_SECONDS until the server fails
//           (HTTP 500+ (timeouts/resets do not count) from the server), never because of slowness
// See README.md.
import http from "k6/http";
import exec from "k6/execution";
import { Counter, Rate, Trend } from "k6/metrics";
import { CONFIG, STEPS, stepAt } from "./config.js";
import { PUBLIC_ENDPOINTS, AI_ENDPOINTS, GUARDIAN_ENDPOINTS } from "./endpoints.js";
import { CLASSES, CRASH_CLASSES } from "./classes.js";
import { buildSummary } from "./report.js";

// Resolve each endpoint's base from its service, and drop ones we can't reach:
//   - fast-dragon AI only when INCLUDE_AI_ANALYSIS=true
//   - express-guardian only when GUARDIAN_BASE_URL is set
//   - any auth:true endpoint only when a COOKIE is set (else it just 401s)
const HAS_COOKIE = CONFIG.cookie !== "";
const HAS_GUARDIAN = CONFIG.guardianBaseUrl !== "";
// AI + portfolio (the `ai` group) only when asked AND the cache is not off.
const INCLUDE_AI = CONFIG.aiActive;
function baseFor(svc) {
  return svc === "eg" ? CONFIG.guardianBaseUrl : CONFIG.baseUrl;
}
const ENDPOINTS = [
  ...PUBLIC_ENDPOINTS,
  ...(INCLUDE_AI ? AI_ENDPOINTS : []),
  ...(HAS_GUARDIAN ? GUARDIAN_ENDPOINTS : []),
]
  .filter((e) => !e.auth || HAS_COOKIE)
  .map((e) => ({ ...e, url: `${baseFor(e.svc)}${e.path}` }));

// Replayed on every request so gated fast-dragon + express-guardian reads work.
const COOKIE_HEADER = HAS_COOKIE ? { Cookie: CONFIG.cookie } : {};

// One counter per outcome class. Every class gets an add (0 or 1) on every
// recorded request so each one is always present in the summary.
const COUNTERS = Object.fromEntries(CLASSES.map((c) => [c, new Counter(`out_${c}`)]));
const errors = new Rate("errors"); // anything that isn't 2xx (server's or generator's)
const hardErrors = new Rate("hard_errors"); // the server failed (crash-type classes only)
const started = new Counter("started"); // requests started, per step (generator health)
const activeVus = new Trend("active_vus"); // parallel users when each request started

function scenario() {
  const base = {
    preAllocatedVUs: CONFIG.preAllocatedVus,
    maxVUs: CONFIG.maxVus,
    gracefulStop: INCLUDE_AI ? CONFIG.aiRequestTimeout : CONFIG.requestTimeout,
  };
  if (CONFIG.mode === "load") {
    return { ...base, executor: "constant-arrival-rate", rate: CONFIG.loadRpm, timeUnit: "1m", duration: `${CONFIG.durationSeconds}s` };
  }
  // Rate is "requests per step": jump to the next level in 1s, hold for the rest of the step.
  const stages = STEPS.flatMap((s) => [
    { target: s.requests, duration: "1s" },
    { target: s.requests, duration: `${CONFIG.stepSeconds - 1}s` },
  ]);
  return { ...base, executor: "ramping-arrival-rate", timeUnit: `${CONFIG.stepSeconds}s`, startRate: CONFIG.startRequests, stages };
}

function thresholds() {
  // Load mode: pass/fail against the healthy limits. Stress mode is expected to
  // go past them, so it only uses the per-step stop rule below.
  const t =
    CONFIG.mode === "load"
      ? { errors: [`rate<=${CONFIG.healthyErrorRate}`], http_req_duration: [`p(95)<=${CONFIG.healthyP95Ms}`] }
      : {};
  // Always-true thresholds: they make k6 keep per-step / per-endpoint
  // sub-metrics for the report.
  const keep = (metric, tag, value, expr) => {
    const key = `${metric}{${tag}:${value}}`;
    (t[key] = t[key] || []).push(expr);
  };
  for (const s of STEPS) {
    keep("http_req_duration", "step", s.index, "max>=0");
    keep("errors", "step", s.index, "rate>=0");
    keep("hard_errors", "step", s.index, "rate>=0");
    keep("started", "step", s.index, "count>=0");
    keep("active_vus", "step", s.index, "max>=0");
    for (const c of CLASSES) keep(`out_${c}`, "step", s.index, "count>=0");

    if (CONFIG.mode === "stress") {
      // The only stop rule: the server failed HTTP 500+ only on more
      // than CRITICAL_ERROR_RATE of a step's requests. Each step is judged on
      // its own requests once the step has finished, so a couple of stray
      // errors early in a step can't stop the test on a small sample.
      const delayAbortEval = `${Math.round(s.end + 1)}s`;
      keep("hard_errors", "step", s.index, { threshold: `rate<=${CONFIG.criticalErrorRate}`, abortOnFail: true, delayAbortEval });
    }
  }
  for (const e of ENDPOINTS) {
    keep("http_req_duration", "name", e.name, "max>=0");
    keep("errors", "name", e.name, "rate>=0");
    for (const c of CLASSES) keep(`out_${c}`, "name", e.name, "count>=0");
  }
  return t;
}

export const options = {
  scenarios: { main: scenario() },
  thresholds: thresholds(),
  summaryTrendStats: ["avg", "min", "med", "p(85)", "p(90)", "p(95)", "p(99)", "p(99.9)", "max"],
  userAgent: "eon-load-test/k6",
};

// Every request lands in exactly one class (see classes.js). k6 network error
// codes: https://grafana.com/docs/k6/latest/javascript-api/error-codes/
const CLIENT_LIMIT = /too many open files|cannot assign requested address|no buffer space|address already in use|resource temporarily unavailable/;
export function classify(res) {
  if (res.status >= 200 && res.status < 300) return "ok";
  if (res.status === 429) return "rate_limited";
  if (res.status >= 500) return "server_5xx";
  if (res.status !== 0) return "other_4xx";
  const code = res.error_code || 0;
  const msg = String(res.error || "").toLowerCase();
  if (msg.includes("context canceled")) return "cancelled"; // k6 stopping the test, not the server
  if (CLIENT_LIMIT.test(msg) || (code >= 1100 && code < 1200)) return "client_limit"; // test machine / DNS
  if (code === 1050 || msg.includes("request timeout")) return "timeout";
  if (code === 1212 || msg.includes("connection refused")) return "refused";
  if (code === 1220 || code === 1201 || msg.includes("reset by peer") || msg.includes("broken pipe") || /\beof\b/.test(msg)) return "reset";
  if (code === 1211 || code === 1210 || msg.includes("no route to host") || msg.includes("network is unreachable") || (msg.includes("dial") && msg.includes("timeout"))) return "unreachable";
  if (code >= 1300 && code < 1400) return "tls";
  return "other_network";
}

const fmtDur = (sec) => `${Math.floor(sec / 60)}m${String(Math.round(sec % 60)).padStart(2, "0")}s`;

// Printed once at the start: what will run, and whether the server's rate
// limiter is on (which would turn most of the run into 429s).
export function setup() {
  const fdCount = ENDPOINTS.filter((e) => e.svc === "fd").length;
  const egCount = ENDPOINTS.filter((e) => e.svc === "eg").length;
  console.log(
    `▶ Combo "${CONFIG.combo}" · mode ${CONFIG.mode} · AI ${INCLUDE_AI ? "on" : "off"}${CONFIG.cacheOff ? " (forced off: cache off)" : ""} · ` +
      `cookie ${HAS_COOKIE ? "set" : "none"} · ${ENDPOINTS.length} endpoints (fast-dragon ${fdCount}, express-guardian ${egCount})`,
  );
  console.log(`▶ fast-dragon ${CONFIG.baseUrl}${HAS_GUARDIAN ? ` · express-guardian ${CONFIG.guardianBaseUrl}` : " · express-guardian not set (GUARDIAN_BASE_URL)"}`);
  if (HAS_GUARDIAN && !HAS_COOKIE) console.warn("⚠ GUARDIAN_BASE_URL is set but COOKIE is empty — express-guardian reads need a session cookie and were skipped.");
  if (CONFIG.mode === "load") {
    console.log(`▶ Fixed ${CONFIG.loadRpm} req/min (${(CONFIG.loadRpm / 60).toFixed(1)} req/s) for ${fmtDur(CONFIG.durationSeconds)}`);
  } else {
    const s1 = STEPS[0];
    const s2 = STEPS[1] || s1;
    console.log(
      `▶ Stress: ${s1.requests} requests in the first ${CONFIG.stepSeconds}s (${s1.rps.toFixed(0)} req/s), ` +
        `+${CONFIG.stepRequests} every ${CONFIG.stepSeconds}s (${s2.rps.toFixed(0)}, ${(s2.rps + CONFIG.stepRequests / CONFIG.stepSeconds).toFixed(0)} … req/s). ` +
        `Stops only when the server returns HTTP 500+ on more than ${(CONFIG.criticalErrorRate * 100).toFixed(2)}% of a step's requests, or after ${fmtDur(CONFIG.maxDurationSeconds)} (up to ${STEPS[STEPS.length - 1].rps.toFixed(0)} req/s). Timeouts, resets and slow responses don't stop it.`,
    );
  }
  const probe = http.get(`${CONFIG.baseUrl}/api/top-coins`, { tags: { name: "setup-probe", group: "setup" }, timeout: "30s", responseType: "none" });
  const limit = probe.headers["Ratelimit-Limit"] || probe.headers["RateLimit-Limit"];
  if (probe.status === 0) console.warn(`⚠ Probe failed: ${probe.error}. Is TARGET_BASE_URL reachable?`);
  else if (limit) console.warn(`⚠ Server rate limit is ON: ${limit} requests per window per IP. You'll get 429s, not real capacity. Set RATE_LIMIT_ENABLED=false on the server.`);
  else console.log(`▶ Probe OK (HTTP ${probe.status}, ${Math.round(probe.timings.duration)}ms); server rate limiter is off.`);
}

// Failing requests are logged as they happen (with the real error message) by
// the first 3 workers only, each at most once every 10 s, so a run with
// thousands of workers still prints a few sample lines, not a flood.
// LOG_ERRORS=false turns it off.
const LOG_ERRORS = (__ENV.LOG_ERRORS || "true").toLowerCase() !== "false";
let lastLog = 0;

export default function () {
  const e = ENDPOINTS[exec.scenario.iterationInTest % ENDPOINTS.length];
  // Elapsed time since the load started (setup excluded).
  const step = stepAt((Date.now() - exec.scenario.startTime) / 1000);
  const tags = { name: e.name, group: e.group, step };
  started.add(1, { step });
  activeVus.add(exec.instance.vusActive, tags);

  const res = http.get(e.url, {
    tags,
    headers: COOKIE_HEADER,
    timeout: e.group === "ai" ? CONFIG.aiRequestTimeout : CONFIG.requestTimeout,
    responseType: "none", // body is read (SSE streams included) but not kept
  });

  const cls = classify(res);
  if (cls === "cancelled") return; // the test is stopping: not a server result

  if (LOG_ERRORS && cls !== "ok" && exec.vu.idInTest <= 3 && Date.now() - lastLog > 10000) {
    lastLog = Date.now();
    const what = res.status === 0 ? `${res.error} (code ${res.error_code})` : `HTTP ${res.status}`;
    console.warn(`✗ step ${step} · ${e.name} · ${cls} · ${what} · ${Math.round(res.timings.duration)}ms`);
  }
  errors.add(cls !== "ok", tags);
  // Generator-side failures say nothing about the server, so they're left out
  // of the server's failure rate.
  if (cls !== "client_limit") hardErrors.add(CRASH_CLASSES.includes(cls), tags);
  for (const c of CLASSES) COUNTERS[c].add(c === cls ? 1 : 0, tags);
}

export function handleSummary(data) {
  return buildSummary(data);
}
