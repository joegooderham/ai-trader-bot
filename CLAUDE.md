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

2. **MCP Server** (`mcp_server/server.py`) — FastAPI service on port 8090 providing market context (economic calendar, sentiment, correlations, volatility, session stats) with 30-min cache. Called by the bot during each scan to enrich trade decisions. Also serves LSTM analytics endpoints.

3. **Health Monitor** (`scripts/health_monitor.py`) — Checks bot, MCP server, IG API reachability, and disk space every 60 seconds. Sends Telegram alerts on failure/recovery.

### Trade Decision Flow

Each 15-minute scan:
1. Fetch candles from IG API (`broker/ig_client.py`) — cached to stay within IG demo's 10k points/week allowance
2. **Save candles to SQLite** (`data/storage.py`) — live broker data feeds into LSTM training
3. Calculate technical indicators (`bot/engine/indicators.py`) — RSI, MACD, Bollinger Bands, EMA crossover, ATR, volume
4. Get LSTM prediction (`bot/engine/lstm/predictor.py`) — BUY/SELL/HOLD with probability
5. Fetch market context from MCP server (`mcp_server/server.py`)
6. Score confidence 0-100% (`bot/engine/confidence.py`) — weighted: LSTM 50%, MACD/RSI 20%, EMA 15%, Bollinger 10%, Volume 5%
7. If score >= 60%, calculate position size (`risk/position_sizer.py`) using ATR-based stops and 2% risk per trade
8. Place trade via IG API, notify via Telegram

### LSTM v2 Architecture

The LSTM model (`bot/engine/lstm/model.py`) uses a 2-layer LSTM with self-attention:
- **18 input features** (`features.py`): standard technicals + day cyclical encoding, RSI rate-of-change, MACD-signal distance, close-vs-range, EMA cross momentum
- **Self-attention mechanism**: weights important timesteps across the 30-candle sequence rather than relying solely on the final hidden state
- **Architecture**: 2-layer LSTM (96 hidden units), attention layer, batch normalization, dropout 0.3, ~119k parameters
- **Training enhancements**: WeightedRandomSampler for class imbalance, ReduceLROnPlateau scheduler, gradient clipping (max_norm=1.0)
- **Model versioning**: each retrain saves a timestamped copy alongside `lstm_v1.pt` for rollback
- Architecture params configurable in `config.yaml` under `lstm:` (hidden_size, num_layers, dropout)

### LSTM Continuous Training

The LSTM retrains automatically on a configurable interval (default 4h, set via `lstm.retrain_interval_minutes` in config.yaml):
- Tops up SQLite with latest candles from yfinance (`trainer.refresh_candles()`)
- Live IG candles are also saved to SQLite every 15-min scan (`source="ig_live"`)
- Trains on 3 months of data by default; extends by 2 weeks per 10% below 50% accuracy (capped at 6 months)
- Hot-reloads the predictor after training — no restart needed
- **Shadow mode** (`lstm.shadow_mode: true`): logs LSTM vs indicator-only scores side by side without affecting trades. Set to `false` to give LSTM its full 50% weight in live confidence scoring.
- Training duration is reported in Telegram so the retrain interval can be tightened towards real-time
- Retrain uses a threading lock so cycles don't stack up

### LSTM Analytics Pipeline

Real-time monitoring of LSTM performance:
- **Prediction logging** — every LSTM prediction is saved to SQLite with pair, direction, confidence, and entry price
- **Outcome resolution** (`resolve_prediction_outcomes`) — hourly job checks 3 subsequent candles to determine if prediction was correct (same logic as training labels)
- **Drift detection** (`bot/engine/lstm/drift.py`) — compares rolling 24h live accuracy vs training accuracy; flags >15% degradation; triggers retrain recommendation
- **Metrics engine** (`bot/analytics/metrics.py`) — computes accuracy at 24h/7d/30d windows, LSTM edge vs indicators, per-pair accuracy, weekly trend
- **Scheduled jobs**: outcome resolution (60min), drift check (30min), analytics snapshot (60min)
- **MCP endpoints**: `/analytics/model`, `/analytics/predictions`, `/analytics/accuracy`, `/analytics/drift`, `/analytics/performance`, `/analytics/summary`

### End-of-Day Rules
- 23:45 UTC: `eod_manager.py` re-scores open positions; only holds overnight if confidence >= 98% AND profitable
- 23:59 UTC: Force-closes all non-held positions, clears candle cache

### Key Modules

| Module | Purpose |
|--------|---------|
| `bot/config.py` | Single config source — loads from `.env` + `config/config.yaml` |
| `broker/ig_client.py` | IG Group REST API client with session auth, candle caching, epic mapping |
| `data/storage.py` | SQLite storage (tables: trades, overnight_holds, candles, predictions, model_metrics, analytics_snapshots) |
| `data/context_writer.py` | Generates `data/LIVE_CONTEXT.md` every 15 min for Claude Projects visibility |
| `notifications/telegram_bot.py` | Outbound Telegram messages — trading bot for trades/reports, system bot for ops/health alerts |
| `notifications/telegram_chat.py` | Inbound Telegram commands: /status, /balance, /datastatus, /accuracy, /model, /drift, /performance |
| `bot/engine/lstm/` | LSTM v2: model (attention), features (18), trainer (weighted sampling), predictor, drift detector |
| `bot/analytics/metrics.py` | Computes rolling LSTM accuracy, edge vs indicators, per-pair performance, weekly trends |
| `bot/instance.py` | Multi-instance heartbeat coordination (single instance currently) |

### Dual Telegram Bots

Two separate Telegram bots keep trading signals and system ops separate:
- **Trading bot** (`TELEGRAM_BOT_TOKEN`): trade opens/closes, daily/weekly reports, overnight holds, trailing stop updates
- **System bot** (`TELEGRAM_BOT_SYS_TOKEN`): health alerts, recovery, data source fallbacks, startup/shutdown, drift alerts, circuit breaker
- If `TELEGRAM_BOT_SYS_TOKEN` is not set, all messages fall back to the trading bot (zero breaking change)
- yfinance fallback alerts are deduplicated — one summary alert when IG fails, one recovery message when all pairs return. Use `/datastatus` for on-demand status.

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/positions` | Open positions with P&L |
| `/balance` | Account funds, equity, margin, available |
| `/pltoday` | Today's realised + unrealised P&L |
| `/plweek` | This week's running total by pair |
| `/history` | Last 10 closed trades with outcome |
| `/close <#>` | Close trade by number |
| `/closeall` | Close all open positions |
| `/closepair EURUSD` | Close a specific pair's position |
| `/closeprofitable` | Close all profitable positions |
| `/closelosing` | Close all losing positions |
| `/pause` / `/resume` | Pause/resume trading |
| `/status` | Bot health, services, open positions |
| `/report` | Trigger daily report on demand |
| `/setconfidence 50` | Adjust min confidence threshold % |
| `/setrisk 2` | Adjust risk per trade % |
| `/settings` | Show all current bot settings |
| `/deploy` | Trigger CI/CD deployment via GitHub Actions |
| `/deploystatus` | Show last 5 deployment runs |
| `/datastatus` | IG vs yfinance data source status per pair |
| `/accuracy` | Rolling LSTM prediction accuracy (7d) |
| `/model` | LSTM model info (version, params, last train) |
| `/drift` | Drift detection status |
| `/performance` | LSTM performance metrics |

## Configuration

- **Environment variables**: Copy `.env.example` to `.env` and fill in IG, Telegram, Anthropic API, and GitHub PAT credentials
- **Trading parameters**: `config/config.yaml` — pairs, timeframes, confidence thresholds, risk settings, schedule times, LSTM architecture
- **Config is loaded once** at import time by `bot/config.py`; changes require restart

## Deployment

- **CI/CD**: GitHub Actions (`.github/workflows/ci.yml`) on push to main — rebuilds and restarts Docker containers on a self-hosted Windows runner
- **All secrets** are stored as GitHub Secrets and passed as environment variables in docker-compose
- **Volumes**: `./data_store`, `./logs`, `./config` are mounted into containers for persistence

## IG Broker Integration Notes

- IG epic mapping (e.g., `EUR_USD` → `CS.D.EURUSD.MINI.IP`) is defined in `broker/ig_client.py`
- Demo account has 10,000 data points/week limit — candle caching strategy keeps usage ~1,320 points/week
- Auth uses CST + X-SECURITY-TOKEN headers with 6-hour auto-refresh
- Migrated from Oanda to IG in March 2026 — no Oanda code or references remain

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

- **Current**: Python, Docker, IG Group API, Telegram, APScheduler, FastAPI, Anthropic Claude API, PyTorch (LSTM)
- **Data**: SQLite (trade history + candle cache), yfinance (historical data + refresh)
- **Secrets**: Injected via GitHub Actions — never hardcode credentials in code or config files

## Backlog

| ID | Title | Description |
|----|-------|-------------|
| BACKLOG-011 | Multi-asset framework | Core infrastructure for trading multiple asset classes: asset class registry, per-class epic mapping, contract sizing per asset type, market hours awareness per exchange, separate config sections per asset class. **Prerequisite:** Forex must be consistently profitable on demo. |
| BACKLOG-017 | Commodities trading | Add Gold (XAU/USD), Silver (XAG/USD), Crude Oil (WTI), Natural Gas via IG. Requires: commodity epic codes, commodity-specific position sizing (different contract sizes), commodity market hours (futures sessions). IG demo supports all of these. |
| BACKLOG-018 | ETF trading | Add major ETFs: SPY, QQQ, IWM, EEM, GLD, TLT via IG. Requires: ETF epic mapping, US market hours (14:30-21:00 UTC), dividend/ex-date awareness, different spread characteristics. |
| BACKLOG-019 | Index trading | Add UK100 (FTSE), US500 (S&P 500), US Tech 100 (Nasdaq), Germany 40 (DAX), Japan 225 via IG. Requires: index epic codes, index-specific volatility profiles, overnight gap risk management. |
| BACKLOG-020 | Crypto trading | Add BTC/USD, ETH/USD, and top alts via IG. Requires: 24/7 market hours (no EOD close), crypto-specific volatility handling, weekend trading support. |
| BACKLOG-021 | Multi-portfolio management | Separate portfolio tracking per asset class: independent P&L, risk limits, confidence thresholds, and LSTM models per portfolio. Dashboard shows per-portfolio performance. Lets each asset class be tuned independently. |
| BACKLOG-022 | Multi-broker support | Abstract the broker layer so the bot can trade across multiple platforms (IG, Interactive Brokers, Alpaca, etc.). Solves API throttling limits on any single broker. Requires: broker interface abstraction in `broker/`, per-broker auth/config, order routing logic (which broker for which asset class), unified position tracking across brokers. |
| BACKLOG-026 | Interactive Brokers integration | Add IBKR as second broker for stocks, ETFs, and indices. Best API for algo trading — no data point limits, WebSocket streaming, fractional shares. Free account, no minimum. Requires: `ib_insync` Python library, TWS/IB Gateway running alongside Docker, IBKR-specific order types (LMT, MKT, STP), different position sizing (shares not lots), US market hours. Phase 2 broker after IG forex is profitable. |
| BACKLOG-027 | Alpaca integration | Add Alpaca for commission-free US stocks + crypto. Very developer-friendly REST + WebSocket API. Free account, no minimum. Requires: `alpaca-trade-api` library, Alpaca OAuth, paper trading mode, crypto 24/7 support. Complements IBKR — use for smaller positions and crypto. |
| BACKLOG-024 | Automated database backups | Back up SQLite database (trade history, candle data, LSTM training data) to free cloud storage on a schedule. Candidates: Cloudflare R2 (10GB free), Backblaze B2 (10GB free), or git-based backup to a private repo. Scheduled nightly via cron job or scheduler. Critical before going live with real money — protects against machine failure. |
| BACKLOG-025 | Cloud hosting / redundancy | Move bot to always-on cloud hosting for 24/7 uptime. Options: Hetzner VPS (€4.50/mo), Oracle Cloud free tier (when available), or cheap mini PC on local network. Include automated deployment and failover from current self-hosted runner. Not urgent while running on demo. |
| BACKLOG-028 | Currency Strength Meter | Rank all 8 currencies by relative strength using DXY + cross-pair data. Filter: don't BUY weakest currency, don't SELL strongest. Zero external API needed — computed from existing price data. |
| BACKLOG-029 | Seasonality Patterns | Analyse historical trade data for day-of-week and month-of-year patterns. Feed seasonal win rate into LSTM features. Zero external data — uses our own SQLite history. |
| BACKLOG-030 | Fix COT data source | CFTC/Nasdaq returning 403. Switch to Barchart.com free COT endpoint as alternative. Same data, different provider. |
| BACKLOG-031 | Forex Factory Calendar | Replace RSS economic calendar with Forex Factory scraping — more granular event times, expected vs actual values, revision data. |
| BACKLOG-032 | CME FedWatch integration | Market-implied rate hike/cut probabilities. Moves forex significantly. Scrape from CME website. |
| BACKLOG-033 | Google Trends sentiment | Track search interest for "recession", "inflation", "forex" via pytrends library. Spikes correlate with volatility. Free, no API key. |
| BACKLOG-034 | Central Bank speech scoring | Track ECB/Fed/BOE speech dates via RSS, score transcripts as hawkish/dovish using FinBERT. Free. |
| BACKLOG-035 | Reddit/social sentiment | Scrape Reddit forex/trading communities for retail sentiment. Free API. |
| BACKLOG-023 | Expand free signal sources | Add more free data feeds into the MCP context and LSTM features. Candidates: DXY (Dollar Index) as USD strength proxy, VIX (fear index) for risk-on/off regime, Treasury yield spreads (2Y/10Y inversion = recession signal), central bank speech calendar (hawkish/dovish NLP scoring), order book depth/liquidity from free APIs, social sentiment (Twitter/Reddit NLP via free APIs), forex factory calendar (more granular than current economic calendar), interbank rates (SOFR, ESTR) for carry trade refinement, seasonal patterns (month-of-year, turn-of-month effects). Each new signal becomes both an MCP confidence modifier and an LSTM input feature. |
| ~~BACKLOG-013~~ | ~~IG Client Sentiment~~ | Done — `mcp_server/client_sentiment.py`, contrarian modifier in `confidence.py` |
| ~~BACKLOG-014~~ | ~~FRED macro data~~ | Done — `mcp_server/fred_macro.py`, interest rate differential bias. Needs `FRED_API_TOKEN` env var. |
| ~~BACKLOG-015~~ | ~~Myfxbook sentiment~~ | Done — `mcp_server/myfxbook_sentiment.py`, community contrarian signal. No API key needed. |
| ~~BACKLOG-016~~ | ~~CFTC COT positioning~~ | Done — `mcp_server/cot_positioning.py`, institutional positioning bias via Nasdaq/Quandl. No API key needed. |

## Pending Actions

- **Branch `claude/telegram-pr-notifications-xfpGo`** is ready to merge into `main` — contains 15 new Telegram commands (close pair/profitable/losing, balance, P&L today/week, history, status, report, setconfidence, setrisk, settings, deploy, deploystatus) plus updated help, docs, and config
- **After merging**: add `GITHUB_PAT` secret in GitHub Settings → Secrets (needs `repo` + `workflow` scopes) for the `/deploy` and `/deploystatus` commands to work
- **Optional**: also add `GITHUB_REPO` secret (defaults to `joegooderham/ai-trader-bot`)

## Important Constraints

- **DEMO mode only** with £500 capital limit — do not change to live without explicit owner instruction
- Minimum trade size: 1 IG mini CFD contract (10,000 currency units)
- `data/LIVE_CONTEXT.md` is auto-generated — do not manually edit
- Telegram bot must poll on the main thread (Python's `set_wakeup_fd` requirement)
- Every directory has a `README.md` with a high-level overview — keep these updated when adding new modules