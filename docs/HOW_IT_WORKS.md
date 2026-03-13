# How Joseph's Forex Bot Works
### A Plain-English Guide — No Jargon

---

## The Big Picture

Every 15 minutes, the bot wakes up and asks the same question for each currency pair:

> *"Is there a good trading opportunity right now, and how confident am I?"*

If the answer is confident enough (60% or above), it places a trade.
If not, it does nothing and waits for the next scan.

Simple as that.

---

## Step by Step — What Happens Each Scan

### 1. Fetch Price Data
The bot asks IG Group: *"Give me the last 60 price bars for EUR/USD."*

A "price bar" contains the open, high, low, and close price for a 1-hour period.
60 bars = about 2.5 days of recent price history.

If IG is unavailable (rate limits, downtime), the bot automatically falls back to Yahoo Finance (yfinance) as a free backup data source. You'll get a Telegram alert when this happens.

Candle data is also stored in a local SQLite database, so the bot checks its cache before making API calls — this keeps IG data usage well within the demo account's 10k points/week limit.

### 2. Calculate Technical Indicators
The bot runs the price data through several standard trading tools:

**RSI (Relative Strength Index)**
Imagine a rubber band. The more stretched it gets, the more likely it snaps back.
RSI measures how "stretched" the price is.
- Below 30 = stretched downward (potential buy)
- Above 70 = stretched upward (potential sell)

**MACD**
Compares a fast-moving average to a slow-moving average of price.
When the fast one crosses the slow one, momentum is shifting.
Think of it like a car — MACD tells you whether the engine is accelerating or braking.

**Bollinger Bands**
Three lines around the price — a middle average and two outer bands.
When price touches the outer bands, it often bounces back to the middle.
Like a ball bouncing off walls.

**EMA Crossover**
Two moving averages — one reacts quickly to price (20-period), one slowly (50-period).
When the fast one is above the slow one, the trend is up.
When it's below, the trend is down.

**ATR (Average True Range)**
Measures how much a price typically moves in one period.
Used to set stop-losses at a sensible distance — not too tight, not too loose.

### 3. Ask the MCP Server for Context
Before deciding, the bot consults its "research desk" — the MCP server.

The MCP server answers questions like:
- *"Is there a major economic announcement in the next 2 hours?"* (If yes, confidence drops — news events cause unpredictable moves)
- *"What's the overall news sentiment for EUR/USD today?"* (Bullish news supports a BUY signal)
- *"Are we already holding GBP/USD? Because EUR/USD moves similarly — we'd be doubling our risk."*
- *"Is the market in a high-volatility regime right now?"* (High volatility = more caution)
- *"Has EUR/USD historically made money during this session?"*

The MCP server then calls Claude AI to pull all this together into an intelligent context summary.

### 4. Calculate the Confidence Score
All the indicator signals and MCP context are combined into a single score: **0 to 100%**.

Here's how the score is built:

```
AI Model Prediction        →  up to 50 points
MACD + RSI consensus       →  up to 20 points
EMA trend alignment        →  up to 15 points
Bollinger Band position    →  up to 10 points
Volume confirmation        →  up to  5 points
─────────────────────────────────────────────
Total before MCP           →  up to 100 points

MCP Context modifier       →  ±0 to ±15 points
(economic events, sentiment, correlations, volatility)
─────────────────────────────────────────────
FINAL SCORE                →  0 to 100%
```

The bot **only trades if the final score is 60% or above**.

### 5. Size the Trade Safely
If the score qualifies, the bot calculates how many IG mini CFD contracts to trade.

The rule: **risk only 2% of your total capital on any single trade**.

Example with £500 capital:
- Maximum loss per trade: £10 (2% of £500)
- Stop-loss is set 1.5 ATR away from entry
- Contract size is calculated so that if the stop-loss is hit, you lose exactly £10
- Minimum is always 1 contract (10,000 currency units)

This is called "position sizing" — it's what prevents any single bad trade from doing real damage.

### 6. Place the Trade
The bot tells IG Group to buy or sell a specific number of contracts, with:
- **Stop-Loss**: The price at which IG will automatically cut the loss
- **Take-Profit**: The price at which IG will automatically bank the profit

Both orders are set on IG's servers. Even if the bot crashes, your position is protected.

### 7. Send You a Telegram Message
Immediately after placing the trade, you get a message on Telegram:
- What pair was traded
- Buy or sell
- Entry price
- Stop-loss and take-profit levels
- The confidence score
- A plain-English explanation of why

---

## End of Day (23:59 UTC Every Night)

The bot closes every open position at 23:59 UTC.

**Why?**
Holding trades overnight introduces "gap risk" — prices can jump sharply when major news breaks outside trading hours. Day traders close everything daily to avoid waking up to unexpected losses.

**The one exception — the 98% Rule:**
At 23:45, the bot re-evaluates every open position one last time.
If a position scores 98% or higher AND is currently profitable, it's held overnight.
The stop-loss is tightened to protect 75% of the current profit.
You get a Telegram message telling you which position was held and why.

This exception is rare. The 98% bar is intentionally very high.

---

## Your Daily Report (00:05 UTC)

Every night after the close, you get a summary:

- How many trades were placed
- How many won vs. lost
- Total profit or loss for the day
- Best and worst performing pair
- Your current account balance
- Any positions held overnight

---

## Your Weekly Report (Sunday 20:00 UTC)

Every Sunday evening, you get a bigger picture:

- Weekly P&L summary
- Win rate over the week
- Which pairs performed best
- **Claude AI's outlook for the coming week** — based on upcoming economic events and current sentiment

---

## How the AI "Learns"

The bot doesn't have memory between restarts, but it does get smarter over time in two ways:

**1. Daily Learning Records**
After every trading day, the MCP server records what market conditions were like and how trades performed. Over 30 days, this builds a rich dataset of what works and what doesn't.

**2. Weekly Claude Analysis**
Every Sunday, Claude AI reviews the week's trading results alongside market context. It identifies patterns — "EUR/USD trades have been more profitable during London session" or "high-volatility periods have led to losses" — and factors these into the confidence scoring going forward.

---

## Why This Isn't Just Pattern Matching

Traditional trading bots spot patterns and act on them blindly.
For example: "RSI below 30 = always buy." That's pattern matching. It works sometimes, fails catastrophically in others.

This bot is different because:

1. **It understands context** — a buy signal during a major news event is completely different from the same signal on a quiet day. The MCP server knows the difference.

2. **Claude AI reasons** — when generating the weekly outlook and analysing trade contexts, Claude doesn't just calculate. It reasons. It considers multiple factors simultaneously and explains its thinking.

3. **Confidence scoring is multi-layered** — no single indicator can trigger a trade. Multiple signals must agree, and external context must support the decision.

4. **It knows what it doesn't know** — when confidence is below 60%, the bot does nothing. Sitting on your hands when the signal is unclear is one of the hardest things in trading. This bot does it consistently.

---

## The Infrastructure — Why Docker?

Docker packages the entire application — code, libraries, configuration — into a container that runs identically everywhere.

Today: your Windows laptop in a shed.
Tomorrow: a £4/month Linux server in a datacentre.
Same command. Same behaviour. Zero reconfiguration.

It also means:
- The bot restarts automatically if it crashes
- Updates are as simple as `git pull && docker-compose up -d --build`
- You always know exactly what version is running

---

*This document is kept updated alongside the codebase.*
