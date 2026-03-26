# Infrastructure & Deployment

---

## Container Architecture

```mermaid
graph TB
    subgraph Docker["🐳 Docker Compose (5 containers)"]
        BOT["🤖 forex-bot<br/>Trading engine<br/>+ Command API :8060<br/>+ Telegram polling"]
        MCP["🔬 mcp-server<br/>Market analysis<br/>:8090"]
        DASH["📊 dashboard<br/>Web UI<br/>:8050"]
        HM["❤️ health-monitor<br/>Watchdog"]
        TUNNEL["🌐 cloudflared<br/>Tunnel to Cloudflare"]
    end

    subgraph Volumes["📁 Mounted Volumes"]
        DS["data_store/<br/>SQLite DB"]
        LOGS["logs/"]
        CFG["config/<br/>config.yaml"]
        DOCS["docs/<br/>Documentation"]
    end

    BOT --> DS & LOGS & CFG
    MCP --> DS
    DASH -.->|"read-only"| DS & CFG & DOCS
    HM --> BOT & MCP
    TUNNEL --> DASH

    style BOT fill:#1e40af,stroke:#3b82f6,color:#fff
    style MCP fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style DASH fill:#059669,stroke:#10b981,color:#fff
```

## CI/CD Pipeline

```mermaid
flowchart LR
    DEV["👨‍💻 Push to main"] --> GH["🐙 GitHub Actions"]
    GH --> RUNNER["🖥️ Self-hosted<br/>Windows runner"]
    RUNNER --> BUILD["docker-compose<br/>up -d --build"]
    BUILD --> LIVE["✅ Live in ~60 seconds"]

    BUILD --> DOCS_JOB["Auto-update docs<br/>(Claude API)"]
    DOCS_JOB --> PUSH["Push README changes"]

    style LIVE fill:#059669,stroke:#10b981,color:#fff
```

## Network Flow

```mermaid
flowchart TD
    USER["👤 User"] -->|"HTTPS"| CF["Cloudflare Edge<br/>SSL + Access (OAuth)"]
    CF -->|"Encrypted tunnel"| TUNNEL["cloudflared container"]
    TUNNEL -->|"HTTP :8050"| DASH["Dashboard"]
    DASH -->|"HTTP :8060"| BOT["Bot Command API"]
    DASH -->|"HTTP :8090"| MCP["MCP Server"]
    BOT -->|"HTTPS"| IG["IG Group API"]
    BOT -->|"HTTPS"| TG["Telegram API"]
    MCP -->|"HTTPS"| FRED["FRED API"]
    MCP -->|"HTTPS"| MFX["Myfxbook"]
    MCP -->|"HTTPS"| COT["CFTC/Nasdaq"]
    MCP -->|"HTTPS"| NEWS["RSS Feeds"]
    MCP -->|"HTTPS"| VIX_I["VIX / DXY<br/>(yfinance)"]
    MCP -->|"HTTPS"| YIELD_I["Treasury Yields<br/>(FRED)"]
    MCP -->|"HTTPS"| FNG_I["Fear & Greed<br/>(CNN)"]
    MCP -->|"Local"| FINBERT_I["FinBERT NLP<br/>(transformers)"]

    style CF fill:#dc2626,stroke:#ef4444,color:#fff
    style DASH fill:#059669,stroke:#10b981,color:#fff
```

## Environment Variables

### Secrets (GitHub Secrets → docker-compose)
| Variable | Service | Purpose |
|----------|---------|---------|
| `IG_API_KEY` | bot, mcp | IG Group broker API key |
| `IG_USERNAME` / `IG_PASSWORD` | bot, mcp | IG authentication |
| `IG_ACCOUNT_ID` | bot, mcp | IG account identifier |
| `TELEGRAM_BOT_TOKEN` | bot | Trading bot Telegram token |
| `TELEGRAM_BOT_SYS_TOKEN` | bot | System bot Telegram token |
| `TELEGRAM_CHAT_ID` | bot | Your Telegram chat ID |
| `ANTHROPIC_API_KEY` | bot, mcp, dashboard | Claude AI API key |
| `FRED_API_TOKEN` | mcp | FRED macro data API key |
| `DASHBOARD_CMD_TOKEN` | bot, dashboard | Shared auth for command API |
| `CLOUDFLARE_TUNNEL_TOKEN` | cloudflared | Tunnel authentication |
| `GITHUB_PAT` | CI | Push permissions for doc updates |

### Hardcoded Defaults (in docker-compose)
| Variable | Value | Service |
|----------|-------|---------|
| `MCP_SERVER_URL` | `http://mcp-server:8090` | dashboard |
| `BOT_COMMAND_URL` | `http://forex-bot:8060` | dashboard |
| `DB_PATH` | `/app/data_store/trader.db` | dashboard |
| `DOCS_DIR` | `/app/docs` | dashboard |
| `IG_ENVIRONMENT` | `demo` | bot, mcp |

## Database

Single SQLite file (`data_store/trader.db`) with tables:

| Table | Rows (est.) | Purpose |
|-------|------------|---------|
| `trades` | ~200 | Every trade opened/closed |
| `candles` | ~10,000 | OHLCV price data |
| `predictions` | growing | LSTM predictions with outcomes |
| `scan_log` | growing | Full audit of every pair evaluation |
| `model_metrics` | ~10 | LSTM training snapshots |
| `analytics_snapshots` | growing | Rolling performance metrics |
| `daily_plans` | ~30 | Claude AI daily trading plans |
| `overnight_holds` | ~5 | Positions held past EOD |

Current size: **~2.2 MB**. Projected: ~200 MB/year.

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `torch` / `pytorch` | LSTM neural network training and inference |
| `transformers` | FinBERT NLP model for news headline sentiment analysis |
| `fastapi` / `uvicorn` | MCP server and dashboard backend |
| `apscheduler` | Job scheduling (scans, retrains, health audits) |
| `yfinance` | Fallback candle data, VIX, DXY feeds |
| `python-telegram-bot` | Telegram bot integration |
| `anthropic` | Claude AI API for chat and analysis |
| `fredapi` | FRED macro data (interest rates, yield spreads) |

## Scheduled Jobs

| Job | Interval | Description |
|-----|----------|-------------|
| Market scan | Every 3 hours | Evaluate all pairs, place trades |
| Position reconciliation | Every 5 minutes | Sync open positions with broker |
| LSTM retrain | Every 4 hours | Retrain model on fresh data |
| Integrity review | Every 3 hours | Check performance, recommend fixes |
| Deep review | Every 6 hours | Comprehensive performance analysis |
| Health audit | Twice daily (09:00 + 17:00 UTC) | System health checks |
| Outcome resolution | Every 60 minutes | Resolve LSTM prediction outcomes |
| Drift check | Every 30 minutes | LSTM accuracy drift detection |
| Analytics snapshot | Every 60 minutes | Rolling performance metrics |
| EOD evaluation | 23:45 UTC | Re-score open positions |
| EOD close | 23:59 UTC | Force close non-held positions |
| Daily report | 00:05 UTC | Send daily P&L report |
