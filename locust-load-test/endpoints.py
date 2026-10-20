"""Public preview GET APIs (fast-dragon), called logged out.
Same list as the k6 service (test/load-test/src/endpoints.js)."""

BTC = "Qwsogvtv82FCd"
ETH = "razxDUgYGNAdQ"

PUBLIC = [
    ("coins-list", "/api/coins-list?start=1&limit=100"),
    ("top-coins", "/api/top-coins"),
    ("leaderboard", "/api/get-leaderboard-list"),
    ("news-metadata", "/api/news-metadata?type=topics"),
    ("market-sentiment", "/api/get-market-sentiment?timePeriod=7d&news=market"),
    ("tickers-sentiment", "/api/get-tickers-sentiment?timePeriod=7d"),
    ("whale-available-coins", "/api/whale/available-coins"),
    ("pm-news", "/api/latest-prediction-markets-news"),
    ("total-market-cap-1y", "/api/total-market-cap/1y"),
    ("total-market-cap-1m", "/api/total-market-cap/1m"),
    ("bitcoin-dominance-1y", "/api/bitcoin-dominance/1y"),
    ("trading-volumes-max", "/api/global-trading-volumes/max"),
    ("trading-volumes-1m", "/api/global-trading-volumes/1m"),
    ("ohlcv-btc-1y", f"/api/ohlcv?uuid={BTC}&timePeriod=1y"),
    ("ohlcv-eth-1y", f"/api/ohlcv?uuid={ETH}&timePeriod=1y"),
    ("ohlcv-btc-sma", f"/api/ohlcv?uuid={BTC}&timePeriod=1y&indicator=sma"),
    ("coin-btc", f"/api/coins/{BTC}"),
    ("markets-btc", f"/api/markets/{BTC}"),
    ("description-btc", f"/api/description/{BTC}"),
    ("description-market-cap", "/api/description/market_cap"),
    ("supply-history-btc", f"/api/get-supply-history?uuid={BTC}&indicator=marketcap&timePeriod=1y"),
    ("dashboard", "/api/dashboard"),
    ("discover-opportunities", "/api/discover/opportunities"),
    ("discover-spot-screening", "/api/discover/spot-screening"),
    ("discover-perp-setups", "/api/discover/perp-setups"),
    ("screener-spot", "/api/screener/spot"),
    ("screener-perps", "/api/screener/perps"),
    ("token-unlocks", "/api/token-unlocks?timePeriod=thisMonth"),
    ("news", "/api/news"),
    ("trending-news", "/api/latest-trending-news"),
    ("whale-transfers", "/api/whale/transfers"),
    ("wishlist", "/api/get-wishlist"),
]

# Only when INCLUDE_AI_ANALYSIS=true. Served from cache; built once on a miss.
AI = [
    ("ai-fundamental-btc", f"/api/fundamental-analysis/{BTC}?indicator=market_cap"),
    ("ai-technical-btc", f"/api/technical-analysis/{BTC}?indicator=sma"),
    ("ai-whale", "/api/whale/analysis"),
    ("demo-portfolio", "/api/portfolio/public-sample"),
]
