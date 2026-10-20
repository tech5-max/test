// Reads and validates the env (k6 __ENV) and works out the load steps.
// Shared by script.js (scenario, thresholds, tagging) and report.js.
//
//   TEST_MODE=load    fixed rate for DURATION
//   TEST_MODE=stress  +STRESS_STEP_REQUESTS every STRESS_STEP_SECONDS until the
//                     server fails: 5xx or no response (or STRESS_MAX_DURATION)

const env = (name, fallback) => {
  const v = __ENV[name];
  return v === undefined || String(v).trim() === "" ? fallback : String(v).trim();
};

function num(name, fallback, { min = 0 } = {}) {
  const raw = env(name, undefined);
  if (raw === undefined) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < min) throw new Error(`${name} must be a number >= ${min} (got "${raw}")`);
  return n;
}

// "90s", "2m", "1m30s", "1h" → seconds
export function parseDuration(name, value) {
  const re = /(\d+(?:\.\d+)?)(h|m|s)/g;
  let total = 0;
  let matched = "";
  let m;
  while ((m = re.exec(value)) !== null) {
    total += Number(m[1]) * { h: 3600, m: 60, s: 1 }[m[2]];
    matched += m[0];
  }
  if (!matched || matched !== value || total <= 0) throw new Error(`${name} must be a duration like 30s, 2m, 1m30s (got "${value}")`);
  return total;
}

function readConfig() {
  const baseUrl = env("TARGET_BASE_URL", "");
  if (!/^https?:\/\/[^/]+/.test(baseUrl)) throw new Error('TARGET_BASE_URL is required, e.g. "https://api.ozak.dev"');

  const mode = env("TEST_MODE", "stress").toLowerCase();
  if (mode !== "load" && mode !== "stress") throw new Error(`TEST_MODE must be "load" or "stress" (got "${mode}")`);

  const cfg = {
    baseUrl: baseUrl.replace(/\/+$/, ""),
    mode,
    includeAi: env("INCLUDE_AI_ANALYSIS", "false").toLowerCase() === "true",
    // 🟢 errors ≤ HEALTHY_ERROR_RATE and p95 ≤ HEALTHY_P95_MS.
    // 🔴 hard failures (5xx or no response) > CRITICAL_ERROR_RATE; stress stops there.
    // 🟡 anything in between. Slowness alone never makes a step 🔴.
    healthyErrorRate: num("HEALTHY_ERROR_RATE", 0.01),
    healthyP95Ms: num("HEALTHY_P95_MS", 2000, { min: 1 }),
    criticalErrorRate: num("CRITICAL_ERROR_RATE", 0.01),
    preAllocatedVus: num("PRE_ALLOCATED_VUS", 500, { min: 1 }),
    maxVus: num("MAX_VUS", 5000, { min: 1 }),
    requestTimeout: env("REQUEST_TIMEOUT", "15s"),
    aiRequestTimeout: env("AI_REQUEST_TIMEOUT", "60s"),
  };
  parseDuration("REQUEST_TIMEOUT", cfg.requestTimeout);
  parseDuration("AI_REQUEST_TIMEOUT", cfg.aiRequestTimeout);

  if (mode === "load") {
    const rps = env("REQUESTS_PER_SECOND", undefined);
    const rpm = env("REQUESTS_PER_MINUTE", undefined);
    if (rps === undefined && rpm === undefined) throw new Error("load mode needs REQUESTS_PER_MINUTE (or REQUESTS_PER_SECOND)");
    cfg.loadRpm = rps !== undefined ? num("REQUESTS_PER_SECOND", 0, { min: 1 }) * 60 : num("REQUESTS_PER_MINUTE", 0, { min: 1 });
    cfg.durationSeconds = parseDuration("DURATION", env("DURATION", "2m"));
  } else {
    cfg.startRequests = num("STRESS_START_REQUESTS", 500, { min: 1 });
    cfg.stepRequests = num("STRESS_STEP_REQUESTS", 500, { min: 1 });
    cfg.stepSeconds = parseDuration("STRESS_STEP_SECONDS", `${env("STRESS_STEP_SECONDS", "10")}s`.replace(/ss$/, "s"));
    if (cfg.stepSeconds < 5) throw new Error("STRESS_STEP_SECONDS must be at least 5");
    cfg.maxDurationSeconds = parseDuration("STRESS_MAX_DURATION", env("STRESS_MAX_DURATION", "30m"));
    cfg.maxSteps = Math.floor(cfg.maxDurationSeconds / cfg.stepSeconds);
    if (cfg.maxSteps < 1) throw new Error("STRESS_MAX_DURATION must be at least one STRESS_STEP_SECONDS");
  }
  return cfg;
}

// Measurement windows; every request is tagged with the step it starts in.
//   load:   one step covering DURATION at the fixed rate
//   stress: step n sends STRESS_START_REQUESTS + (n-1) × STRESS_STEP_REQUESTS
//           requests over STRESS_STEP_SECONDS
function buildSteps(cfg) {
  if (cfg.mode === "load") {
    return [{ index: 1, requests: (cfg.loadRpm * cfg.durationSeconds) / 60, rps: cfg.loadRpm / 60, start: 0, end: cfg.durationSeconds }];
  }
  const steps = [];
  for (let n = 1; n <= cfg.maxSteps; n++) {
    const requests = cfg.startRequests + (n - 1) * cfg.stepRequests;
    steps.push({
      index: n,
      requests,
      rps: requests / cfg.stepSeconds,
      start: (n - 1) * cfg.stepSeconds,
      end: n * cfg.stepSeconds,
    });
  }
  return steps;
}

export const CONFIG = readConfig();
export const STEPS = buildSteps(CONFIG);

export function stepAt(elapsedSeconds) {
  const s = STEPS.find((st) => elapsedSeconds >= st.start && elapsedSeconds < st.end);
  return s ? String(s.index) : String(STEPS[STEPS.length - 1].index);
}

// Shared level rule (report + live view).
//   hardErrRate = (5xx + no response) / requests; errRate = any non-2xx / requests
export function levelOf(errRate, p95, hardErrRate) {
  if (hardErrRate > CONFIG.criticalErrorRate) return "critical";
  if (errRate <= CONFIG.healthyErrorRate && p95 <= CONFIG.healthyP95Ms) return "healthy";
  return "stressed";
}
