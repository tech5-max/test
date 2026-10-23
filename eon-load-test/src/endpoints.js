// eon endpoints for the full load test, across both services.
//   svc: "fd" -> TARGET_BASE_URL (fast-dragon)
//        "eg" -> GUARDIAN_BASE_URL (express-guardian)
//   auth: true   -> only sent when a COOKIE is set (the route needs a session)
//   group: "public" | "ai" | "guardian" | "write"
//   rotate: true -> the "{UUID}" token in the path is replaced per request with a
//                   rotating real coin uuid (fetched in setup from /api/coins-list)
//   method/body  -> for write routes (POST/PUT/DELETE); GET + no body otherwise
//
// fast-dragon list is the verified public-preview GET surface. express-guardian
// reads are all ensureAuthenticated. Payments and auth routes are excluded. Write
// routes are only included when INCLUDE_WRITE_ROUTES=true (see config.js).

const BTC = "Qwsogvtv82FCd";
const ETH = "razxDUgYGNAdQ";

// Fallback uuids if setup can't fetch a live list.
export const DEFAULT_UUIDS = [BTC, ETH];

// ---- fast-dragon: public preview GETs (no uuid param) ----
const FD_PUBLIC = [
  { name: "coins-list", path: "/api/coins-list?start=1&limit=100" },
  { name: "top-coins", path: "/api/top-coins" },
  { name: "leaderboard", path: "/api/get-leaderboard-list" },
  { name: "news-metadata", path: "/api/news-metadata?type=topics" },
  { name: "market-sentiment", path: "/api/get-market-sentiment?timePeriod=7d&news=market" },
  { name: "tickers-sentiment", path: "/api/get-tickers-sentiment?timePeriod=7d" },
  { name: "whale-available-coins", path: "/api/whale/available-coins" },
  { name: "pm-news", path: "/api/latest-prediction-markets-news" },
  { name: "total-market-cap-1y", path: "/api/total-market-cap/1y" },
  { name: "total-market-cap-1m", path: "/api/total-market-cap/1m" },
  { name: "bitcoin-dominance-1y", path: "/api/bitcoin-dominance/1y" },
  { name: "trading-volumes-max", path: "/api/global-trading-volumes/max" },
  { name: "trading-volumes-1m", path: "/api/global-trading-volumes/1m" },
  { name: "description-market-cap", path: "/api/description/market_cap" },
  { name: "dashboard", path: "/api/dashboard" },
  { name: "discover-opportunities", path: "/api/discover/opportunities" },
  { name: "discover-spot-screening", path: "/api/discover/spot-screening" },
  { name: "discover-perp-setups", path: "/api/discover/perp-setups" },
  { name: "screener-spot", path: "/api/screener/spot" },
  { name: "screener-perps", path: "/api/screener/perps" },
  { name: "token-unlocks", path: "/api/token-unlocks?timePeriod=thisMonth" },
  { name: "news", path: "/api/news" },
  { name: "trending-news", path: "/api/latest-trending-news" },
  { name: "whale-transfers", path: "/api/whale/transfers" },
  { name: "wishlist", path: "/api/get-wishlist" },
].map((e) => ({ ...e, svc: "fd", group: "public", auth: false }));

// ---- fast-dragon: uuid-param GETs — rotate across real coins ----
const FD_UUID = [
  { name: "ohlcv-1y", path: "/api/ohlcv?uuid={UUID}&timePeriod=1y" },
  { name: "ohlcv-sma", path: "/api/ohlcv?uuid={UUID}&timePeriod=1y&indicator=sma" },
  { name: "coin", path: "/api/coins/{UUID}" },
  { name: "markets", path: "/api/markets/{UUID}" },
  { name: "description-coin", path: "/api/description/{UUID}" },
  { name: "supply-history", path: "/api/get-supply-history?uuid={UUID}&indicator=marketcap&timePeriod=1y" },
].map((e) => ({ ...e, svc: "fd", group: "public", auth: false, rotate: true }));

// ---- fast-dragon: AI analyses (only when INCLUDE_AI_ANALYSIS=true) ----
// Kept on BTC and NOT rotated on purpose: a fresh uuid is a cache miss that triggers
// a paid OpenAI generation, so rotating these would be expensive.
const FD_AI = [
  { name: "ai-fundamental-btc", path: `/api/fundamental-analysis/${BTC}?indicator=market_cap` },
  { name: "ai-technical-btc", path: `/api/technical-analysis/${BTC}?indicator=sma` },
  { name: "ai-whale", path: "/api/whale/analysis" },
  { name: "demo-portfolio", path: "/api/portfolio/public-sample" },
].map((e) => ({ ...e, svc: "fd", group: "ai", auth: false }));

// ---- express-guardian: light reads ----
// eg-session works logged-out (returns session status), so auth:false; the rest are
// ensureAuthenticated (auth:true → only sent with a COOKIE).
const EG_AUTH = [
  { name: "eg-session", path: "/api/auth/session", auth: false },
  { name: "eg-profile", path: "/api/user/profile" },
  { name: "eg-get-credits", path: "/api/user/get-credits" },
  { name: "eg-is-buyer", path: "/api/user/is-buyer" },
  { name: "eg-onboarding-tour", path: "/api/user/onboarding-tour" },
  { name: "eg-notifications", path: "/api/notifications/" },
  { name: "eg-tokens-from", path: "/api/tokens/from" },
  { name: "eg-tokens-to", path: "/api/tokens/to" },
].map((e) => ({ svc: "eg", group: "guardian", auth: true, ...e }));

// ---- WRITE routes (INCLUDE_WRITE_ROUTES=true only; need COOKIE) ----
// Stage only, one test user. All writes hit that user's row and are idempotent-ish:
//   - watchlist add/delete self-no-op after the first (the coin is/ isn't already there)
//   - profile-update writes only when a field actually changes, so a fixed body
//     settles after the first call
// They still write to stage userDetailsDB, so keep the rate modest.
const EG_FD_WRITES = [
  // fast-dragon watchlist CRUD on users.wishlist
  { name: "w-watchlist-add", svc: "fd", method: "POST", path: "/api/discover/watchlist", body: { coin_uuid: BTC } },
  { name: "w-watchlist-update", svc: "fd", method: "PUT", path: "/api/discover/watchlist", body: { coin_uuids: [BTC, ETH] } },
  { name: "w-watchlist-delete", svc: "fd", method: "DELETE", path: `/api/discover/watchlist/${BTC}` },
  // express-guardian profile update (fixed body → no DB write after it matches)
  { name: "w-profile-update", svc: "eg", method: "POST", path: "/api/user/update-profile", body: { fullname: "Load Test" } },
].map((e) => ({ ...e, group: "write", auth: true, body: e.body ? JSON.stringify(e.body) : undefined }));

export const PUBLIC_ENDPOINTS = [...FD_PUBLIC, ...FD_UUID];
export const AI_ENDPOINTS = FD_AI;
export const GUARDIAN_ENDPOINTS = EG_AUTH;
export const WRITE_ENDPOINTS = EG_FD_WRITES;
