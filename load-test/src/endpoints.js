// Public preview GET APIs (fast-dragon), called logged out.
// Source: predictive-ai-web-services/docs/PUBLIC_PREVIEW_FRONTEND_API.md

const BTC = "Qwsogvtv82FCd";
const ETH = "razxDUgYGNAdQ";

export const PUBLIC_ENDPOINTS = [
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
  { name: "ohlcv-btc-1y", path: `/api/ohlcv?uuid=${BTC}&timePeriod=1y` },
  { name: "ohlcv-eth-1y", path: `/api/ohlcv?uuid=${ETH}&timePeriod=1y` },
  { name: "ohlcv-btc-sma", path: `/api/ohlcv?uuid=${BTC}&timePeriod=1y&indicator=sma` },
  { name: "coin-btc", path: `/api/coins/${BTC}` },
  { name: "markets-btc", path: `/api/markets/${BTC}` },
  { name: "description-btc", path: `/api/description/${BTC}` },
  { name: "description-market-cap", path: "/api/description/market_cap" },
  { name: "supply-history-btc", path: `/api/get-supply-history?uuid=${BTC}&indicator=marketcap&timePeriod=1y` },
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
].map((e) => ({ ...e, group: "public" }));

// Only when INCLUDE_AI_ANALYSIS=true. Served from cache; built once on a miss.
export const AI_ENDPOINTS = [
  { name: "ai-fundamental-btc", path: `/api/fundamental-analysis/${BTC}?indicator=market_cap` },
  { name: "ai-technical-btc", path: `/api/technical-analysis/${BTC}?indicator=sma` },
  { name: "ai-whale", path: "/api/whale/analysis" },
  { name: "demo-portfolio", path: "/api/portfolio/public-sample" },
].map((e) => ({ ...e, group: "ai" }));
