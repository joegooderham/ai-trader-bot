# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered Forex day trading bot using Claude AI for trade decisions, IG Group as broker, and Telegram for notifications. Runs as three Docker containers: the trading bot, an MCP analysis server, and a health monitor.

## Build & Run Commands

```bash
# Start all services (bot + MCP server + health monitor)
docker-compose up -d

# Rebuild and restart (what CI does on push to main)
docker-compose down && docker-compose build --no-cache && docker-compose up -d

# View logs
docker-compose logs -f forex-bot
docker-compose logs -f mcp-server
docker-compose logs -f health-monitor

# Run the bot locally (requires .env configured)
python -m bot.scheduler

# Run the MCP server locally
python -m mcp_server.server

# Run health monitor locally
python -m scripts.health_monitor

# Install dependencies
pip install -r requirements.txt
```

There is no test suite, linter config, or Makefile in this project.

## Architecture

The system has three runtime processes orchestrated via docker-compose:

1. **Trading Bot** (`bot/scheduler.py`) — Entry point. Uses APScheduler to run market scans every 15 min, position monitoring every 5 min, and EOD operations at 23:45/23:59 UTC. Telegram polling runs on the main thread; the scheduler runs in a daemon thread.

2. **MCP Server** (`mcp_server/server.py`) — FastAPI service on port 8090 providing market context (economic calendar, sentiment, correlations, volatility, session stats) with 30-min cache. Called by the bot during each scan to enrich trade decisions.

3. **Health Monitor** (`scripts/health_monitor.py`) — Checks bot, MCP server, IG API reachability, and disk space every 60 seconds. Sends Telegram alerts on failure/recovery.

### Trade Decision Flow

Each 15-minute scan:
1. Fetch candles from IG API (`broker/ig_client.py`) — cached to stay within IG demo's 10k points/week allowance
2. Calculate technical indicators (`bot/engine/indicators.py`) — RSI, MACD, Bollinger Bands, EMA crossover, ATR, volume
3. Fetch market context from MCP server (`mcp_server/server.py`)
4. Score confidence 0-100% (`bot/engine/confidence.py`) — weighted: LSTM 50%, MACD/RSI 20%, EMA 15%, Bollinger 10%, Volume 5%
5. If score >= 60%, calculate position size (`risk/position_sizer.py`) using ATR-based stops and 2% risk per trade
6. Place trade via IG API, notify via Telegram

### End-of-Day Rules
- 23:45 UTC: `eod_manager.py` re-scores open positions; only holds overnight if confidence >= 98% AND profitable
- 23:59 UTC: Force-closes all non-held positions, clears candle cache

### Key Modules

| Module | Purpose |
|--------|---------|
| `bot/config.py` | Single config source — loads from `.env` + `config/config.yaml` |
| `broker/ig_client.py` | IG Group REST API client with session auth, candle caching, epic mapping |
| `data/storage.py` | JSON file-based trade history (no database) |
| `data/context_writer.py` | Generates `data/LIVE_CONTEXT.md` every 15 min for Claude Projects visibility |
| `notifications/telegram_bot.py` | All outbound Telegram messages (trades, reports, alerts) |
| `notifications/telegram_chat.py` | Inbound Telegram command handler (runs on main thread) |
| `bot/instance.py` | Multi-instance heartbeat coordination (single instance currently) |

## Configuration

- **Environment variables**: Copy `.env.example` to `.env` and fill in IG, Telegram, and Anthropic API credentials
- **Trading parameters**: `config/config.yaml` — pairs, timeframes, confidence thresholds, risk settings, schedule times
- **Config is loaded once** at import time by `bot/config.py`; changes require restart

## Deployment

- **CI/CD**: GitHub Actions (`.github/workflows/ci.yml`) on push to main — rebuilds and restarts Docker containers on a self-hosted Windows runner
- **All secrets** are stored as GitHub Secrets and passed as environment variables in docker-compose
- **Volumes**: `./data`, `./logs`, `./config` are mounted into containers for persistence

## IG Broker Integration Notes

- IG epic mapping (e.g., `EUR_USD` → `CS.D.EURUSD.MINI.IP`) is defined in `broker/ig_client.py`
- Demo account has 10,000 data points/week limit — candle caching strategy keeps usage ~1,320 points/week
- Auth uses CST + X-SECURITY-TOKEN headers with 6-hour auto-refresh
- The bot was recently migrated from Oanda to IG; `broker/oanda_client.py` is legacy/unused

## Git Workflow Rules

### Branching
- **NEVER commit directly to `main` or `develop`** — all merges to `main` require a PR
- Always create a branch for every change using one of these prefixes:
  - `feature/*` — new functionality
  - `fix/*` — bug fixes
  - `hotfix/*` — urgent production fixes

### Commit Messages
Follow this format strictly:
```
type(scope): short description

- What changed and why
- Reference backlog item: BACKLOG-XXX
```
Types: `feat`, `fix`, `hotfix`, `refactor`, `docs`, `chore`, `ci`

Example:
```
feat(broker): add candle caching to reduce IG data usage

- Cache candles in memory with TTL matching timeframe duration
- Top up with only 3 new candles instead of re-fetching 60
- Stays within IG demo's 10k points/week limit
- Ref: BACKLOG-042
```

### Pre-Commit Checks
Always run a syntax check before committing:
```bash
python -m py_compile <changed_file.py>
```
Or check all Python files at once:
```bash
python -m compileall -q bot/ broker/ mcp_server/ notifications/ risk/ data/ scripts/
```

### Code Comments
Always add detailed inline comments explaining **why** decisions were made, not just what the code does. Document the reasoning behind trade logic, risk thresholds, API workarounds, and architectural choices.

## Tech Stack

- **Current**: Python, Docker, IG Group API, Telegram, APScheduler, FastAPI, Anthropic Claude API
- **Coming soon**: yfinance (market data), SQLite (replacing JSON storage)
- **Secrets**: Injected via GitHub Actions — never hardcode credentials in code or config files

## Important Constraints

- **DEMO mode only** with £500 capital limit — do not change to live without explicit owner instruction
- Minimum trade size: 1 IG mini CFD contract (10,000 currency units)
- `data/LIVE_CONTEXT.md` is auto-generated — do not manually edit
- Telegram bot must poll on the main thread (Python's `set_wakeup_fd` requirement)
