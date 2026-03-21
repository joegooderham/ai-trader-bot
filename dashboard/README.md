# dashboard/

Interactive web dashboard for the AI Trader Bot. Full trading control, AI chat, analytics, and monitoring — protected by Cloudflare Access (Google OAuth).

## Structure

| Path | Purpose |
|------|---------|
| `backend/app.py` | FastAPI server — REST API for trade data, command proxy (to bot on port 8060), AI chat (Anthropic API), analytics (proxied from MCP), wiki, config. Serves the built React frontend as static files. |
| `backend/requirements.txt` | Python dependencies (fastapi, httpx, anthropic, yfinance, etc.) |
| `frontend/` | React SPA built with Vite + Tailwind CSS |
| `frontend/src/pages/` | Page components (see below) |
| `frontend/src/components/` | Reusable UI components (see below) |
| `frontend/src/hooks/` | Custom hooks: `useApi.js` (GET with auto-refresh), `useCommand.js` (POST with loading/error state) |
| `Dockerfile` | Multi-stage build: Node (React build) → Python (runtime) |

## Pages

| Page | Route | Purpose |
|------|-------|---------|
| Overview | `/` | Today's stats, 30-day P&L chart, intraday P&L chart, open positions |
| Positions | `/positions` | Open positions with close buttons (individual, by pair, all, profitable, losing), pause/resume |
| Trades | `/trades` | Closed trade history (paginated, filterable) |
| Trade Journal | `/journal` | Expandable trade cards with reasoning, confidence breakdown, R:R, duration |
| LSTM Analytics | `/analytics` | Model info, prediction accuracy, drift detection, performance metrics |
| Heatmap | `/heatmap` | Pair x hour win rate heatmap (colour-coded 24h grid) |
| Sessions | `/sessions` | Performance by forex session (Sydney, Tokyo, London, New York) |
| AI Chat | `/chat` | Messenger-style chat with Claude — full trading history context injected |
| Mystic-Wolf | `/what-if` | Replay historical trades with different settings, compare actual vs simulated P&L |
| Remediation | `/remediation` | Pending integrity recommendations with approve/reject cards |
| Config | `/config` | Live config editor (8 runtime params with sliders) + read-only full YAML |
| Summary | `/summary` | 7d/30d/all-time stats, per-pair breakdown, Claude AI daily plan |
| Wiki | `/wiki` | Project documentation from GitHub wiki (auto-pulled every 30 min) |
| Backlog | `/backlog` | Project roadmap |

## Components

| Component | Purpose |
|-----------|---------|
| `Layout.jsx` | Sidebar navigation (grouped: Trading/Analytics/Tools/Docs) with remediation badge count |
| `TradeControls.jsx` | Pause/resume, close all/profitable/losing buttons with disabled direction/pair badges |
| `ConfirmModal.jsx` | Reusable confirmation dialog for destructive actions |
| `Toast.jsx` | Success/error toast notifications + `useToast()` hook |
| `LivePLChart.jsx` | Intraday cumulative P&L chart (Recharts, 30s refresh) |
| `StatCard.jsx` | Reusable stat card (label, value, sub-text) |
| `PLBadge.jsx` | Formatted P&L display with colour (green/red) |
| `RunningPL.jsx` | Running total P&L in the header |
| `ErrorBoundary.jsx` | React error boundary |

## API Endpoints

### Read-Only (from SQLite + MCP)
| Endpoint | Description |
|----------|-------------|
| `GET /api/overview` | Today's stats, open positions, all-time P&L |
| `GET /api/positions/live` | Open positions with current prices and unrealized P&L |
| `GET /api/trades` | Closed trade history (paginated, filterable) |
| `GET /api/trades/{id}/detail` | Full trade detail with reasoning and breakdown |
| `GET /api/charts/pl-history` | Daily P&L for charting |
| `GET /api/charts/pl-intraday` | Hourly P&L data points for today |
| `GET /api/analytics/*` | LSTM metrics (proxied from MCP server) |
| `GET /api/analytics/heatmap` | Pair x hour win rate data |
| `GET /api/analytics/sessions` | Performance by forex session |
| `GET /api/wiki` | List wiki pages |
| `GET /api/config` | Read-only config.yaml values |
| `GET /api/health` | Dashboard health check |

### Bot Commands (proxied to forex-bot:8060)
| Endpoint | Description |
|----------|-------------|
| `GET /api/cmd/status` | Bot status: paused, disabled directions/pairs, config values |
| `GET /api/cmd/balance` | Account balance from IG broker |
| `GET /api/cmd/remediation` | Pending remediation recommendations |
| `POST /api/cmd/pause` | Pause trading |
| `POST /api/cmd/resume` | Resume trading |
| `POST /api/cmd/close-all` | Close all positions |
| `POST /api/cmd/close-pair` | Close positions for a specific pair |
| `POST /api/cmd/close-profitable` | Close profitable positions |
| `POST /api/cmd/close-losing` | Close losing positions |
| `POST /api/cmd/close/{deal_id}` | Close a specific position |
| `POST /api/cmd/config` | Change config at runtime |
| `POST /api/cmd/remediation/{id}/approve` | Approve a recommendation |
| `POST /api/cmd/remediation/{id}/reject` | Reject a recommendation |

### AI Chat
| Endpoint | Description |
|----------|-------------|
| `POST /api/chat` | Send message to Claude with full trading history context |

### Analysis
| Endpoint | Description |
|----------|-------------|
| `POST /api/analysis/what-if` | Replay trades with hypothetical settings |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_SERVER_URL` | `http://mcp-server:8090` | MCP server URL |
| `BOT_COMMAND_URL` | `http://forex-bot:8060` | Bot command API URL |
| `DB_PATH` | `/app/data_store/trader.db` | Path to SQLite database |
| `CONFIG_PATH` | `/app/config/config.yaml` | Path to trading config |
| `DASHBOARD_CMD_TOKEN` | (empty) | Shared auth token for bot command API |
| `ANTHROPIC_API_KEY` | (empty) | Anthropic API key for AI chat |
| `WIKI_REPO_URL` | GitHub wiki URL | Wiki git repo to clone |

## Running

```bash
# As part of the full stack
docker-compose up -d dashboard

# Backend only (for development)
cd backend && uvicorn app:app --reload --port 8050

# Frontend only (for development, proxies API to localhost:8050)
cd frontend && npm install && npm run dev
```
