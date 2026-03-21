# 🤖 Joseph's Forex Bot

An AI-powered Forex day trading bot that runs 24/7, makes intelligent trade decisions using Claude AI, and keeps you informed via Telegram.

---

## What This Bot Does

- **Trades automatically** — scans 10 currency pairs every 5 minutes for high-confidence opportunities (85% threshold)
- **9 data sources per decision** — LSTM neural network, technical indicators, IG client sentiment, Myfxbook community sentiment, CFTC institutional positioning, FRED macro rates, economic calendar, volatility regime, session performance
- **Self-healing** — automated remediation system detects problems (losing streaks, direction failures), recommends fixes, and lets you approve via Telegram inline buttons or the web dashboard
- **Interactive dashboard** — real-time positions, AI chat, trade journal, heatmap, session analysis, config editor, remediation panel (Cloudflare Access protected)
- **Stays within your budget** — never puts more than your set capital at risk
- **Closes daily** — all positions closed at 23:59 UTC unless the overnight hold threshold (65%) is met
- **Keeps you informed** — Telegram alerts for every trade, plus daily/weekly reports and integrity scans

---

## Quick Start (5 Steps)

### Step 1 — Prerequisites

Install these on your Windows machine:

| Tool | Download | Purpose |
|------|----------|---------|
| **Docker Desktop** | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) | Runs the bot in a container |
| **Git** | [git-scm.com](https://git-scm.com/) | Version control |
| **VS Code** (optional) | [code.visualstudio.com](https://code.visualstudio.com/) | Editing config files |

### Step 2 — Clone the Repo

Open a terminal (Command Prompt or PowerShell) and run:

```bash
git clone https://github.com/YOUR_USERNAME/ai-trader-bot.git
cd ai-trader-bot
```

### Step 3 — Create Your API Keys

You need three free accounts:

**A) IG Group (Broker)**
1. Go to [ig.com](https://www.ig.com) → Open a Demo Account
2. Log in → My IG → Settings → API → Create API Key
3. Note your **API Key**, **Username**, **Password**, and **Account ID**

**B) Telegram (Notifications)**
1. Open Telegram → search for `@BotFather`
2. Send `/newbot` → follow prompts → copy the **Bot Token**
3. Send any message to your new bot
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
5. Find `"chat":{"id":XXXXXXXX}` — that number is your **Chat ID**

**C) Anthropic (Claude AI)**
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. API Keys → Create Key → copy it

### Step 4 — Configure the Bot

Copy the example config file:

```bash
# Windows (Command Prompt)
copy .env.example .env

# Windows (PowerShell)
Copy-Item .env.example .env
```

Open `.env` in a text editor and fill in your values:

```env
IG_API_KEY=your_api_key_here
IG_USERNAME=your_username_here
IG_PASSWORD=your_password_here
IG_ACCOUNT_ID=your_account_id_here
IG_ENVIRONMENT=demo                  # Keep as 'demo' for demo trading
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_claude_key
MAX_CAPITAL=500                      # Maximum £ in open trades at once
```

### Step 5 — Start the Bot

```bash
docker-compose up -d
```

That's it. The bot is now running. You'll receive a Telegram message confirming it started.

---

## Checking the Bot is Running

```bash
# See all running containers
docker-compose ps

# Watch live logs from the trading bot
docker-compose logs -f forex-bot

# Watch MCP server logs
docker-compose logs -f mcp-server
```

---

## Stopping the Bot

```bash
docker-compose down
```

---

## Updating the Bot

```bash
git pull
docker-compose up -d --build
```

---

## Project Structure

```
ai-trader-bot/
│
├── bot/                        # Main trading bot
│   ├── scheduler.py            ← Entry point. Runs all scheduled tasks
│   ├── config.py               ← Loads all settings and API keys
│   └── engine/
│       ├── indicators.py       ← Calculates RSI, MACD, Bollinger Bands, etc.
│       ├── confidence.py       ← Scores trade signals (0–100%)
│       └── lstm/               ← Neural network for trade direction prediction
│           ├── model.py        ← PyTorch LSTM architecture (64 hidden, 3-class)
│           ├── features.py     ← 12 normalised features from OHLCV data
│           ├── trainer.py      ← Continuous training pipeline with adaptive data
│           └── predictor.py    ← Inference wrapper for live predictions
│
├── broker/
│   └── ig_client.py            ← All IG Group API calls (prices, orders, etc.)
│
├── mcp_server/
│   └── server.py               ← Provides market context to the AI
│
├── notifications/
│   ├── telegram_bot.py         ← Sends all Telegram messages
│   └── telegram_chat.py        ← Telegram command handler (interactive)
│
├── risk/
│   ├── position_sizer.py       ← Calculates safe trade sizes
│   └── eod_manager.py          ← Manages end-of-day closes & 98% rule
│
├── data/
│   ├── storage.py              ← SQLite trade & candle storage
│   └── context_writer.py       ← Generates LIVE_CONTEXT.md every 15min
│
├── scripts/
│   └── health_monitor.py       ← Alerts if anything goes wrong
│
├── config/
│   └── config.yaml             ← All trading parameters (edit this to customise)
│
├── .env.example                ← Template for your API keys
├── .env                        ← Your actual keys (NEVER commit this)
├── docker-compose.yml          ← Production services
├── docker-compose.uat.yml      ← UAT / testing services
├── Dockerfile                  ← Builds the main bot container
├── Dockerfile.mcp              ← Builds the MCP server container
└── requirements.txt            ← Python dependencies
```

---

## Understanding the Confidence Score

Every trade the bot considers gets scored from 0–100%. Here's what goes into that score:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| LSTM Neural Network | 50% | AI prediction of price direction (continuously retrained) |
| MACD + RSI | 20% | Is momentum shifting? Is price overbought/oversold? |
| EMA Trend | 15% | Is the short-term trend aligned with the signal? |
| Bollinger Bands | 10% | Is price at an extreme? |
| Volume | 5% | Is the signal backed by real market activity? |
| MCP Context | ±bonus | Economic events, news sentiment, correlations |

The bot only trades if the score is **60% or above** (configurable).

The bot holds a position overnight only if the score is **98% or above** AND the position is profitable.

---

## The 98% Overnight Rule

By default, **all positions are closed at 23:59 UTC every day**.

The only exception: if the AI re-scores a position at 23:45 and it hits 98%+ confidence AND is currently profitable, it's held overnight with a tightened stop-loss.

This should happen rarely — perhaps 2–3 times per month at most.

---

## LSTM Neural Network — Continuous Training

The bot includes a PyTorch LSTM model that predicts trade direction (BUY/SELL/HOLD) and contributes 50% of the confidence score.

### How It Trains

The LSTM retrains automatically on a rolling interval (default: every 4 hours, configurable in `config.yaml`). Each training cycle:

1. **Tops up data** — fetches the latest H1 candles from yfinance since the last stored candle
2. **Builds features** — 12 normalised indicators per candle (RSI, MACD, Bollinger %B, EMA distances, ATR, volume, hour encoding)
3. **Labels data** — looks ahead 3 candles; BUY if price rises > 1 ATR, SELL if it falls > 1 ATR, HOLD otherwise
4. **Trains** — 30-candle sequences, early stopping on validation loss (patience=7)
5. **Reloads** — the live predictor hot-swaps to the new model without restarting the bot

### Adaptive Data Window

Training starts with 3 months of data. If validation accuracy falls below 50%, the window automatically extends by 2 weeks for every 10% below threshold (capped at 6 months):

| Val Accuracy | Extra Data | Total Window |
|---|---|---|
| 50%+ | none | 3 months |
| 40% | +2 weeks | ~3.5 months |
| 30% | +4 weeks | ~4 months |
| 20% | +6 weeks | ~4.5 months |

### Two Data Sources Feed the Model

- **IG live candles** — every 15-min market scan saves candles to SQLite (`ig_live` source), so real broker data flows in continuously
- **yfinance refresh** — each retrain cycle tops up from the latest stored candle to now, filling any gaps

### Shadow Mode

When `shadow_mode: true` in config.yaml (default), every scan logs LSTM-enhanced vs indicator-only scores side by side **without the LSTM affecting actual trades**. This lets you validate the model before letting it drive real decisions. Set `shadow_mode: false` once you're confident the LSTM is adding value.

### Manual Training & Backtesting

```bash
# Trigger a training run inside Docker
docker exec ai-trader-bot python -m bot.engine.lstm.trainer

# Run backtest — compares LSTM vs indicator-only on historical data
docker exec ai-trader-bot python -m bot.engine.lstm.backtest
```

Or use `/backtest` in Telegram to run the simulation and get results in chat.

---

## Telegram Commands

Send these to your bot in Telegram:

| Command | What it does |
|---------|-------------|
| `/today` | Today's trades and P&L |
| `/positions` | Currently open positions |
| `/health` | System health status |
| `/plan` | Tomorrow's trading plan |
| `/stats` | All-time performance stats |
| `/query <question>` | Query trade database in plain English |
| `/devops` | Today's code changes (git log) |
| `/backtest` | Run LSTM vs indicator-only simulation on historical data |
| `/fallbacktest` | Test yfinance backup data source |
| `/help` | Show all commands |

Or just send a plain English question like *"How did EUR/USD do this week?"*

---

## Telegram Messages You'll Receive

| Message | When |
|---------|------|
| 🚀 Bot Started | Every time Docker container starts |
| 📈 Trade Opened | Every new trade (with reasoning) |
| ✅ Trade Closed (profit) | Every winning trade |
| ❌ Trade Closed (loss) | Every losing trade |
| 🌙 Overnight Hold | When 98% rule triggers |
| 📊 Daily Report | Every night after 23:59 close |
| 📊 Weekly Report | Every Sunday at 20:00 UTC |
| ⚠️ Health Alert | If bot crashes or goes offline |
| 🧠 LSTM Retrained | After each training cycle (with accuracy + duration) |
| 🛠 Dev Activity | After code changes are deployed |

---

## Customising the Bot

All trading parameters are in `config/config.yaml`. You can edit:

- Which currency pairs to trade
- Minimum confidence score to trade (default: 60%)
- Maximum open positions (default: 5)
- Risk per trade (default: 2% of capital)
- Scan frequency (default: every 15 minutes)
- Report times

After editing, restart the bot:
```bash
docker-compose restart forex-bot
```

---

## Month 1 — What to Expect

The first month is **demo trading only** — no real money.

The bot is learning:
- Which signals work best on which pairs
- Which sessions are most profitable
- How to calibrate confidence scores

**What you should do:**
- Read the daily Telegram reports
- Note which pairs are performing well
- Don't switch to live money until you've seen at least 4 weeks of consistent results

---

## Moving to Linux / Cloud Server

When you're ready to move off Windows, it's just:

```bash
# On the new server
git clone https://github.com/YOUR_USERNAME/ai-trader-bot.git
cd ai-trader-bot
cp .env.example .env    # Fill in your keys
docker-compose up -d
```

Your trade history in `data_store/` can be copied across too.

---

## Troubleshooting

**Bot won't start:**
```bash
docker-compose logs forex-bot
```
Check for missing .env values.

**Not receiving Telegram messages:**
- Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
- Make sure you've sent at least one message to your bot

**IG connection error:**
- Check IG_API_KEY and IG_USERNAME are correct
- Make sure IG_ENVIRONMENT=demo (not live) for demo trading
- IG demo accounts have a 10k data points/week limit — the bot caches candles to stay within this

**Out of disk space:**
```bash
docker system prune   # Remove unused Docker images
```

---

## Important Disclaimers

- This bot trades with real money when configured with a live IG account
- Forex trading involves substantial risk of loss
- Past performance does not guarantee future results
- Start with demo trading (IG demo account) for at least 4 weeks
- Never trade with money you cannot afford to lose

---

*Built by Claude AI for Joseph | v1.0 | March 2026*
