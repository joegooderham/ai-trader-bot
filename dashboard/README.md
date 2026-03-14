# dashboard/

Web dashboard for the AI Trader Bot. Read-only view of trades, positions, LSTM analytics, and the project wiki.

## Structure

| Path | Purpose |
|------|---------|
| `backend/app.py` | FastAPI server — REST API for trade data, analytics (proxied from MCP), wiki (git clone), config. Also serves the built React frontend as static files. |
| `backend/requirements.txt` | Python dependencies for the backend |
| `frontend/` | React SPA built with Vite + Tailwind CSS |
| `frontend/src/pages/` | Page components: Overview, Positions, TradeHistory, Analytics, Wiki, Config |
| `frontend/src/components/` | Reusable UI components: Layout, StatCard, PLBadge |
| `frontend/src/hooks/useApi.js` | Data fetching hook with auto-refresh |
| `Dockerfile` | Multi-stage build: Node (React build) → Python (runtime) |

## Running

```bash
# As part of the full stack
docker-compose up -d dashboard

# Backend only (for development)
cd backend && uvicorn app:app --reload --port 8050

# Frontend only (for development, proxies API to localhost:8050)
cd frontend && npm install && npm run dev
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/overview` | Today's stats, open positions, all-time P&L |
| `GET /api/positions` | Current open positions |
| `GET /api/trades` | Closed trade history (paginated, filterable) |
| `GET /api/charts/pl-history` | Daily P&L for charting |
| `GET /api/analytics/*` | LSTM metrics (proxied from MCP server) |
| `GET /api/wiki` | List wiki pages |
| `GET /api/wiki/{page}` | Rendered wiki page (markdown → HTML) |
| `GET /api/config` | Read-only config.yaml values |
| `GET /api/health` | Dashboard health check |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_SERVER_URL` | `http://mcp-server:8090` | MCP server URL (change when hosting remotely) |
| `DB_PATH` | `/app/data_store/trades.db` | Path to SQLite database |
| `CONFIG_PATH` | `/app/config/config.yaml` | Path to trading config |
| `WIKI_REPO_URL` | GitHub wiki URL | Wiki git repo to clone |
| `WIKI_PULL_INTERVAL` | `1800` | Wiki refresh interval (seconds) |

## Designed for Portability

The dashboard runs in its own container with no dependencies on the trading bot code. It connects to the MCP server via URL and reads SQLite via mounted volume. This means it can be deployed on a separate server (e.g., Oracle Cloud free tier) by:

1. Pointing `MCP_SERVER_URL` to the MCP server's public URL (via Cloudflare Tunnel)
2. Replicating or remotely mounting the SQLite database
3. Running the dashboard container standalone
