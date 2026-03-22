# dashboard/backend/

FastAPI server powering the dashboard. Serves the REST API and the built React frontend as static files.

| File | Purpose |
|------|---------|
| `app.py` | Main FastAPI app — REST API endpoints for trade data (SQLite), bot commands (proxied to forex-bot:8060), AI chat (Anthropic API), analytics (proxied from MCP:8090), wiki (git clone), and enhanced analytics (heatmap, sessions, journal, what-if). |
| `requirements.txt` | Python dependencies: fastapi, uvicorn, httpx, anthropic, yfinance, markdown, pyyaml, loguru |

## Key Endpoint Groups

- `GET /api/*` — Read-only trade data, positions, charts from SQLite
- `POST /api/cmd/*` — Bot commands proxied to forex-bot:8060 (pause, close, config)
- `POST /api/chat` — AI chat with Claude (full trading history injected as context)
- `POST /api/analysis/*` — What-if simulator
- `GET /api/analytics/*` — LSTM metrics proxied from MCP server
- `GET /api/wiki/*` — Project wiki from GitHub (auto-pulled every 30 min)
