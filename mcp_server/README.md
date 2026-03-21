# mcp_server/

FastAPI service providing market context and analytics APIs. Runs as its own Docker container on port 8090.

| File | Purpose |
|------|---------|
| `server.py` | Main FastAPI app. Endpoints: `/context/{pair}` (market context for trade decisions), `/weekly-outlook` (Claude AI weekly analysis), `/test-fallback` (yfinance health check), `/analytics/*` (6 analytics endpoints for dashboards and Telegram). 30-minute cache on context requests. |
| `economic_calendar.py` | Fetches upcoming high-impact economic events that could move currency pairs. |
| `sentiment.py` | Gauges market sentiment for each pair using news and positioning data. |
| `correlations.py` | Provides correlation warnings when the bot considers opening correlated positions. |
| `volatility.py` | Determines the current volatility regime (low/normal/high) for position sizing adjustments. |
| `session_stats.py` | Historical performance stats by trading session (London, New York, Tokyo, Sydney). |
| `client_sentiment.py` | IG Client Sentiment — retail positioning contrarian indicator. Already authenticated via IG API. |
| `fred_macro.py` | FRED macro data — interest rate differentials for carry trade bias. Requires `FRED_API_TOKEN` env var. 6-hour cache. |
| `myfxbook_sentiment.py` | Myfxbook community sentiment — ~100k connected accounts, cross-validates IG sentiment. No API key needed. 30-min cache. |
| `cot_positioning.py` | CFTC COT data — weekly institutional positioning (hedge funds, banks) via Nasdaq/Quandl. No API key needed. 24-hour cache. |

## Analytics Endpoints
| Endpoint | Purpose |
|----------|---------|
| `GET /analytics/model` | Current model version, accuracy, architecture |
| `GET /analytics/predictions` | Recent predictions with outcomes |
| `GET /analytics/accuracy?window=7d` | Rolling accuracy by pair/direction |
| `GET /analytics/drift` | Live drift detection status |
| `GET /analytics/performance?window=7d` | LSTM edge, accuracy trend |
| `GET /analytics/summary` | Aggregated dashboard overview |
