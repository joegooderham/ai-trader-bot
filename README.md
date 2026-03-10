# 🤖 Joseph's Forex Bot

An AI-powered Forex day trading bot that runs 24/7, makes intelligent trade decisions using Claude AI, and keeps you informed via Telegram.

---

## What This Bot Does

- **Trades automatically** — scans 5 currency pairs every 15 minutes for opportunities
- **Uses real AI reasoning** — not just pattern matching. Claude AI analyses market context and explains *why* each trade was made
- **Stays within your budget** — never puts more than your set capital at risk
- **Closes daily** — all positions closed at 23:59 UTC unless the 98% confidence rule is met
- **Keeps you informed** — Telegram alerts for every trade, plus daily and weekly reports

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

**A) OANDA (Broker)**
1. Go to [oanda.com](https://www.oanda.com) → Open an Account → Practice Account
2. Log in → My Account → Manage API Access → Generate Token
3. Note your **API Token** and **Account ID**

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
OANDA_API_TOKEN=your_token_here
OANDA_ACCOUNT_ID=your_account_id_here
OANDA_ENVIRONMENT=practice          # Keep as 'practice' for demo trading
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_claude_key
MAX_CAPITAL=500                     # Maximum £ in open trades at once
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
docker-compose down
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
│       └── confidence.py       ← Scores trade signals (0–100%)
│
├── broker/
│   └── oanda_client.py         ← All OANDA API calls (prices, orders, etc.)
│
├── mcp_server/
│   └── server.py               ← Provides market context to the AI
│
├── notifications/
│   └── telegram_bot.py         ← Sends all Telegram messages
│
├── risk/
│   ├── position_sizer.py       ← Calculates safe trade sizes
│   └── eod_manager.py          ← Manages end-of-day closes & 98% rule
│
├── data/
│   └── storage.py              ← Saves trade history locally
│
├── scripts/
│   └── health_monitor.py       ← Alerts if anything goes wrong
│
├── config/
│   └── config.yaml             ← All trading parameters (edit this to customise)
│
├── .env.example                ← Template for your API keys
├── .env                        ← Your actual keys (NEVER commit this)
├── docker-compose.yml          ← Defines all services
├── Dockerfile                  ← Builds the main bot container
├── Dockerfile.mcp              ← Builds the MCP server container
└── requirements.txt            ← Python dependencies
```

---

## Understanding the Confidence Score

Every trade the bot considers gets scored from 0–100%. Here's what goes into that score:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| LSTM Neural Network | 50% | AI prediction of price direction |
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

Your trade history in `data/` can be copied across too.

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

**OANDA connection error:**
- Check OANDA_API_TOKEN is correct
- Make sure OANDA_ENVIRONMENT=practice (not live) for demo

**Out of disk space:**
```bash
docker system prune   # Remove unused Docker images
```

---

## Important Disclaimers

- This bot trades with real money when configured with a live OANDA account
- Forex trading involves substantial risk of loss
- Past performance does not guarantee future results
- Start with demo trading (OANDA practice account) for at least 4 weeks
- Never trade with money you cannot afford to lose

---

*Built by Claude AI for Joseph | v1.0 | March 2026*
