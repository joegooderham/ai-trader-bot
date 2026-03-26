# Web Dashboard

Interactive trading dashboard at **aitradefintech.com**, protected by Cloudflare Access (Google OAuth).

---

## Pages

```mermaid
graph TB
    subgraph Trading["📈 Trading"]
        O["Overview<br/>Today's P&L, charts, positions"]
        P["Positions<br/>Live positions with close buttons"]
        T["Trades<br/>History with pagination"]
        J["Journal<br/>Expandable cards with reasoning"]
        S["Scan Log<br/>Every evaluation with full context"]
        CAL["Calendar<br/>Monthly P&L grid"]
    end

    subgraph Analytics["📊 Analytics"]
        LA["LSTM Analytics<br/>Model accuracy, drift"]
        HM["Heatmap<br/>Pair × hour win rate"]
        SESS["Sessions<br/>London/NY/Tokyo/Sydney"]
        CORR["Correlations<br/>10×10 pair matrix"]
        RISK["Risk Exposure<br/>Portfolio risk breakdown"]
    end

    subgraph Tools["🛠️ Tools"]
        CHAT["AI Chat<br/>Ask Claude anything"]
        MW["Mystic Wolf<br/>What-if simulator"]
        REM["Remediation<br/>Approve/reject fixes"]
        CONF["Config<br/>Live parameter editor"]
        HEALTH["Health Audit<br/>System health checks"]
        INTEG["Integrity<br/>Performance reviews"]
    end

    subgraph Docs["📚 Docs"]
        SUM["Summary<br/>Stats + daily plan"]
        WIKI["Wiki<br/>All documentation"]
        BL["Backlog<br/>Roadmap"]
    end

    style Trading fill:#1e40af,stroke:#3b82f6,color:#fff
    style Analytics fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style Tools fill:#059669,stroke:#10b981,color:#fff
    style Docs fill:#d97706,stroke:#f59e0b,color:#fff
```

## Key Features

### Trade Controls
The Positions page includes a control bar:
- **Pause/Resume** — stop or restart trading instantly
- **Close All** — emergency close every position
- **Close Profitable/Losing** — selective closures
- **Per-position Close** buttons on each card
- **Disabled badges** — shows blocked directions/pairs with re-enable buttons

### AI Chat
Messenger-style interface with Claude. Every message gets live trading context injected:
- All daily P&L history
- Per-pair and per-direction performance
- Last 20 closed trades
- Open positions
- Bot status and config
- LSTM model info

Conversation persists across page navigation (stored in sessionStorage).

### Config Editor
Change 8 runtime parameters with sliders — takes effect immediately, persists to YAML:
- Min confidence to trade
- Risk per trade %
- Overnight hold threshold
- Stop-loss ATR multiplier
- Trailing stop activation/trail ATR
- Take-profit ratio
- LSTM shadow mode toggle

### Mystic Wolf (What-If Simulator)
Replays historical trades with hypothetical settings. Shows:
- Actual vs simulated P&L side-by-side
- Which trades would have been filtered and why
- Per-pair impact breakdown
- Filter reason counts

### Calendar
Monthly grid showing daily P&L at a glance. Each day shows:
- Net P&L (colour-coded green/red)
- Trade count, wins, losses, breakeven
- Month navigation with totals

## Architecture

```mermaid
flowchart LR
    BROWSER["🌐 Browser"] --> CF["🔒 Cloudflare Access<br/>Google OAuth"]
    CF --> DASH["📊 Dashboard<br/>React + FastAPI :8050"]
    DASH -->|"GET"| DB[("SQLite<br/>(read-only)")]
    DASH -->|"GET"| MCP["MCP Server<br/>:8090"]
    DASH -->|"POST"| BOT["Bot Command API<br/>:8060"]
    DASH -->|"POST"| CLAUDE["Anthropic API<br/>(AI Chat)"]

    style DASH fill:#059669,stroke:#10b981,color:#fff
```

### Health Audit
Scheduled twice daily (09:00 + 17:00 UTC). Checks:
- Bot and MCP server responsiveness
- IG API connectivity
- LSTM model age and accuracy
- Disk space and database size
- Open position reconciliation

Results displayed on the Health Audit page with status badges and historical trend.

### Integrity Reviews
Runs every 3 hours (aligned with market scans) plus a deep review every 6 hours. Analyses:
- Win rate trends by pair and direction
- P&L trajectory and drawdown levels
- LSTM prediction accuracy vs baseline
- Remediation recommendation history

## Tech Stack

- **Frontend**: React 18, Vite 5, Tailwind CSS 3, Recharts
- **Backend**: FastAPI (Python), serves API + built static files
- **Auth**: Cloudflare Access with Google OAuth
- **Hosting**: Docker container, Cloudflare Tunnel for HTTPS
