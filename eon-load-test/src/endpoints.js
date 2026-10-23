// eon endpoints for the full load test, across both services.
//   svc: "fd" -> TARGET_BASE_URL (fast-dragon)
//        "eg" -> GUARDIAN_BASE_URL (express-guardian)
//   auth: true   -> only sent when a COOKIE is set (the route needs a session)
//   group: "public" | "paid" | "ai" | "guardian" | "write"
//   rotate: true -> the "{UUID}" token in the path is replaced per request with a
//                   rotating real coin uuid (fetched in setup from /api/coins-list)
//   method/body  -> for write routes (POST/PUT/DELETE); GET + no body otherwise
//
// PUBLIC = pure DB/Redis (plus the free news/sentiment providers). PAID = routes that
// call paid upstreams (CoinRanking, Moralis, CoinGlass, CMC) on a cache miss; these
// and the AI routes are gated behind INCLUDE_AI_ANALYSIS (and excluded on cache-off),
// so a default or cache-off run never hits a paid provider.

const BTC = "Qwsogvtv82FCd";
const ETH = "razxDUgYGNAdQ";

// Fallback uuids if setup can't fetch a live list.
export const DEFAULT_UUIDS = [BTC, ETH];

// ---- fast-dragon: PUBLIC — pure DB/Redis (always tested) ----
const FD_PUBLIC = [
  { name: "coins-list", path: "/api/coins-list?start=1&limit=100" },
  { name: "top-coins", path: "/api/top-coins" },
  { name: "leaderboard", path: "/api/get-leaderboard-list" },
  { name: "description-market-cap", path: "/api/description/market_cap" },
  { name: "discover-opportunities", path: "/api/discover/opportunities" },
  { name: "discover-spot-screening", path: "/api/discover/spot-screening" },
  { name: "screener-spot", path: "/api/screener/spot" },
  { name: "wishlist", path: "/api/get-wishlist" },
].map((e) => ({ ...e, svc: "fd", group: "public", auth: false }));

// ---- fast-dragon: NEWS/SENTIMENT — external news providers on a miss ----
// Gated behind INCLUDE_AI_ANALYSIS (and cache-off), same as PAID/AI.
const FD_NEWS = [
  { name: "news", path: "/api/news" },
  { name: "trending-news", path: "/api/latest-trending-news" },
  { name: "news-metadata", path: "/api/news-metadata?type=topics" },
  { name: "market-sentiment", path: "/api/get-market-sentiment?timePeriod=7d&news=market" },
  { name: "tickers-sentiment", path: "/api/get-tickers-sentiment?timePeriod=7d" },
  { name: "pm-news", path: "/api/latest-prediction-markets-news" },
].map((e) => ({ ...e, svc: "fd", group: "news", auth: false }));

// ---- fast-dragon: PUBLIC uuid-param GETs (DB-backed) — rotate across real coins ----
const FD_UUID = [
  { name: "coin", path: "/api/coins/{UUID}" },
  { name: "markets", path: "/api/markets/{UUID}" },
  { name: "description-coin", path: "/api/description/{UUID}" },
].map((e) => ({ ...e, svc: "fd", group: "public", auth: false, rotate: true }));

// ---- fast-dragon: PAID — call CoinRanking / Moralis / CoinGlass / CMC on a miss ----
// Gated behind INCLUDE_AI_ANALYSIS (and cache-off), same as the AI routes, so a
// default / cache-off run never spends on these providers.
const FD_PAID = [
  // CoinRanking
  { name: "ohlcv-1y", path: "/api/ohlcv?uuid={UUID}&timePeriod=1y", rotate: true },
  { name: "ohlcv-sma", path: "/api/ohlcv?uuid={UUID}&timePeriod=1y&indicator=sma", rotate: true },
  { name: "supply-history", path: "/api/get-supply-history?uuid={UUID}&indicator=marketcap&timePeriod=1y", rotate: true },
  { name: "total-market-cap-1y", path: "/api/total-market-cap/1y" },
  { name: "total-market-cap-1m", path: "/api/total-market-cap/1m" },
  { name: "bitcoin-dominance-1y", path: "/api/bitcoin-dominance/1y" },
  { name: "trading-volumes-max", path: "/api/global-trading-volumes/max" },
  { name: "trading-volumes-1m", path: "/api/global-trading-volumes/1m" },
  { name: "token-unlocks", path: "/api/token-unlocks?timePeriod=thisMonth" },
  { name: "dashboard", path: "/api/dashboard" }, // CoinRanking + CMC + Moralis
  // Moralis
  { name: "whale-available-coins", path: "/api/whale/available-coins" },
  { name: "whale-transfers", path: "/api/whale/transfers" },
  // CoinGlass
  { name: "screener-perps", path: "/api/screener/perps" },
  { name: "discover-perp-setups", path: "/api/discover/perp-setups" },
].map((e) => ({ ...e, svc: "fd", group: "paid", auth: false }));

// ---- fast-dragon: AI analyses (OpenAI + Moralis) — same gate ----
const FD_AI = [
  { name: "ai-fundamental-btc", path: `/api/fundamental-analysis/${BTC}?indicator=market_cap` },
  { name: "ai-technical-btc", path: `/api/technical-analysis/${BTC}?indicator=sma` },
  { name: "ai-whale", path: "/api/whale/analysis" },
  { name: "demo-portfolio", path: "/api/portfolio/public-sample" },
].map((e) => ({ ...e, svc: "fd", group: "ai", auth: false }));

// ---- express-guardian: light reads ----
// eg-session works logged-out (auth:false); the rest are ensureAuthenticated.
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
const EG_FD_WRITES = [
  { name: "w-watchlist-add", svc: "fd", method: "POST", path: "/api/discover/watchlist", body: { coin_uuid: BTC } },
  { name: "w-watchlist-update", svc: "fd", method: "PUT", path: "/api/discover/watchlist", body: { coin_uuids: [BTC, ETH] } },
  { name: "w-watchlist-delete", svc: "fd", method: "DELETE", path: `/api/discover/watchlist/${BTC}` },
  { name: "w-profile-update", svc: "eg", method: "POST", path: "/api/user/update-profile", body: { fullname: "Load Test" } },
].map((e) => ({ ...e, group: "write", auth: true, body: e.body ? JSON.stringify(e.body) : undefined }));

export const PUBLIC_ENDPOINTS = [...FD_PUBLIC, ...FD_UUID];
// Everything gated behind INCLUDE_AI_ANALYSIS: paid upstreams + external news.
export const PAID_ENDPOINTS = [...FD_PAID, ...FD_NEWS];
export const AI_ENDPOINTS = FD_AI;
export const GUARDIAN_ENDPOINTS = EG_AUTH;
export const WRITE_ENDPOINTS = EG_FD_WRITES;
