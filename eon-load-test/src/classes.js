// Outcome classes for every request (k6 script, report and live views use these).
// "cancelled" (the tool stopping the test) is never recorded, so it isn't here.

export const CLASSES = [
  "ok", // 2xx
  "server_5xx", // server answered 5xx
  "timeout", // server didn't answer within REQUEST_TIMEOUT
  "refused", // connection refused (nothing listening / backlog full)
  "reset", // connection dropped mid-request (reset, broken pipe, EOF)
  "unreachable", // couldn't reach the server (dial timeout, no route)
  "tls", // TLS handshake failed
  "other_network", // any other network error from the server side
  "client_limit", // the TEST MACHINE ran out of something (sockets, files, DNS)
  "rate_limited", // 429
  "other_4xx", // other 4xx
];

// The server "crashed": ONLY an actual HTTP 500+ response counts. A crash decides
// 🔴 and stops a stress test. Timeouts, resets, refused and other no-response errors
// are treated as the server being under stress (🟡), NOT a crash, so a slow or
// connection-dropping server (or a slow edge) doesn't get called a crash.
export const CRASH_CLASSES = ["server_5xx"];

export const CLASS_LABELS = {
  ok: "OK",
  server_5xx: "server errors (5xx)",
  timeout: "timeouts (server didn't answer in time)",
  refused: "connection refused",
  reset: "connection reset/dropped",
  unreachable: "server unreachable",
  tls: "TLS handshake failures",
  other_network: "other network errors",
  client_limit: "test-machine limit (sockets/files/DNS)",
  rate_limited: "rate-limited (429)",
  other_4xx: "other 4xx",
};
