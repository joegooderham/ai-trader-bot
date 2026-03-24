# How Joseph's Forex Bot Works
### A Plain-English Guide — No Jargon

---

## The Big Picture

Every 3 hours, the bot wakes up and asks the same question for each currency pair:

> *"Is there a good trading opportunity right now, and how confident am I?"*

If the answer is confident enough (85% or above), it places a trade.
If not, it does nothing and waits for the next scan.

Simple as that.

```mermaid
flowchart LR
    A["⏰ Wake up<br/>every 3 hours"] --> B["📊 Check 10<br/>currency pairs"]
    B --> C{"Confidence<br/>≥ 85%?"}
    C -->|Yes| D["💰 Place trade"]
    C -->|No| E["⏭️ Skip it"]
    D --> F["📱 Alert on Telegram"]

    style D fill:#059669,stroke:#10b981,color:#fff
    style E fill:#6b7280,stroke:#9ca3af,color:#fff
```

---

## Step by Step — What Happens Each Scan

### 1. Fetch Price Data
The bot asks IG Group: *"Give me the last 60 price bars for EUR/USD."*

A "price bar" contains the open, high, low, and close price for a 1-hour period.
60 bars = about 2.5 days of recent price history.

If IG is unavailable, the bot automatically falls back to Yahoo Finance as a free backup. You'll get a Telegram alert when this happens.

### 2. Calculate Technical Indicators
The bot runs the price data through several standard trading tools:

```mermaid
graph LR
    PRICE["60 Price Bars"] --> RSI["RSI<br/>Overbought/<br/>Oversold"]
    PRICE --> MACD["MACD<br/>Momentum<br/>Direction"]
    PRICE --> BB["Bollinger<br/>Bands<br/>Price Range"]
    PRICE --> EMA["EMA Cross<br/>Trend<br/>Direction"]
    PRICE --> ATR["ATR<br/>Volatility<br/>Level"]
    PRICE --> VOL["Volume<br/>Signal<br/>Strength"]

    style RSI fill:#1e40af,stroke:#3b82f6,color:#fff
    style MACD fill:#1e40af,stroke:#3b82f6,color:#fff
    style BB fill:#1e40af,stroke:#3b82f6,color:#fff
    style EMA fill:#1e40af,stroke:#3b82f6,color:#fff
    style ATR fill:#1e40af,stroke:#3b82f6,color:#fff
    style VOL fill:#1e40af,stroke:#3b82f6,color:#fff
```

**RSI** — Like a rubber band. The more stretched the price gets, the more likely it snaps back.
**MACD** — Like a car's engine. Tells you whether momentum is accelerating or braking.
**Bollinger Bands** — Price bounces between walls. Touching the outer wall often means a reversal.
**EMA Crossover** — When the fast average crosses the slow one, the trend is shifting.
**ATR** — How much the price typically moves. Used to set stop-losses at a sensible distance.

### 3. Ask the AI Brain (LSTM Neural Network)
The bot has a real neural network that looks at the last 30 hours of data (25 features per hour) and predicts: **will the price go up, down, or sideways?**

This contributes **50% of the confidence score** — it's the single biggest input.

The LSTM retrains every 4 hours on fresh data, and it learns from the bot's actual trade results: winning patterns get reinforced, losing patterns get corrected.

### 4. Check the Market Context (9 Data Sources)
Before deciding, the bot consults its "research desk" — the MCP server:

```mermaid
graph TD
    MCP["🔬 MCP Server"] --> EC["📅 Economic Calendar<br/>Major news events?"]
    MCP --> NS["📰 News Sentiment<br/>Bullish or bearish?"]
    MCP --> IG["👥 IG Client Sentiment<br/>What are retail traders doing?"]
    MCP --> MFX["👥 Myfxbook Sentiment<br/>100k traders' positions"]
    MCP --> COT["🏦 CFTC COT Data<br/>What are hedge funds doing?"]
    MCP --> FRED["💵 FRED Macro<br/>Interest rate differentials"]
    MCP --> VOL["📊 Volatility Regime<br/>Calm or stormy?"]
    MCP --> SESS["🕐 Session Stats<br/>Good time for this pair?"]
    MCP --> CORR["🔗 Correlation Risk<br/>Already holding similar?"]

    style MCP fill:#7c3aed,stroke:#8b5cf6,color:#fff
```

### 5. Calculate the Confidence Score

All signals are combined into a single score: **0 to 100%**.

```mermaid
pie title Confidence Score Components
    "LSTM Neural Network" : 50
    "MACD + RSI" : 20
    "EMA Trend" : 15
    "Bollinger Bands" : 10
    "Volume" : 5
```

Then the 9 MCP context signals adjust it — boosting aligned signals, penalising conflicting ones. The bot **only trades if the final score is 85% or above**.

### 6. Size the Trade Safely
The rule: **risk only 2% of capital on any single trade** (£10 on £500).

- Stop-loss set at 2.0× ATR from entry (adapts to volatility)
- Take-profit set at 2:1 reward-to-risk ratio
- Position size calculated so hitting the stop-loss loses exactly 2%

### 7. Place the Trade & Notify
The bot places the trade on IG with a stop-loss and take-profit, then sends you a Telegram message with the full details and reasoning.

---

## End of Day (23:59 UTC)

```mermaid
flowchart TD
    A["23:45 UTC<br/>Re-evaluate all positions"] --> B{"Confidence ≥ 65%<br/>AND profitable?"}
    B -->|Yes| C["🌙 Hold overnight<br/>Tighten stop to protect 50% profit"]
    B -->|No| D["Close position"]
    D --> E["23:59 UTC<br/>Force close everything remaining"]
    C --> F["📱 Telegram: Overnight hold alert"]
    E --> G["00:05 UTC<br/>Send daily report"]

    style C fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style E fill:#dc2626,stroke:#ef4444,color:#fff
```

---

## Self-Healing: Automated Remediation

The bot monitors its own performance and fixes problems without you needing to intervene:

```mermaid
flowchart TD
    A["🔍 Integrity Review<br/>Every 3 hours"] --> B{"Problems<br/>detected?"}
    B -->|No| C["✅ All clear"]
    B -->|Yes| D["🔬 Diagnose root cause"]
    D --> E["📱 Send recommendation<br/>via Telegram buttons"]
    E --> F{"Your choice"}
    F -->|"✅ Approve"| G["Apply fix instantly<br/>No restart needed"]
    F -->|"❌ Reject"| H["Dismiss"]

    D --> I{"Weekly P&L < -£50?"}
    I -->|Yes| J["🚨 Auto-pause trading<br/>No approval needed"]

    style J fill:#dc2626,stroke:#ef4444,color:#fff
    style G fill:#059669,stroke:#10b981,color:#fff
```

---

## The Dashboard

A full web dashboard at **aitradefintech.com**, protected by Google login:

| Section | What you see |
|---------|-------------|
| **Overview** | Today's P&L, intraday chart, open positions |
| **Positions** | Live positions with close buttons |
| **Calendar** | Monthly grid — P&L per day at a glance |
| **Trade Journal** | Every trade with full reasoning and breakdown |
| **AI Chat** | Ask Claude anything about your trading |
| **Heatmap** | Which pairs work at which times |
| **Config** | Change settings with sliders — takes effect immediately |
| **Mystic Wolf** | "What if I had used different settings last week?" simulator |

---

## Infrastructure

```mermaid
graph LR
    GH["🐙 GitHub<br/>Push code"] -->|"Auto-deploy"| RUNNER["🖥️ Self-hosted<br/>Windows PC"]
    RUNNER --> DOCKER["🐳 Docker<br/>5 containers"]
    DOCKER --> CF["🔒 Cloudflare<br/>HTTPS + Auth"]
    CF --> WEB["🌐 aitradefintech.com"]

    style GH fill:#1e40af,stroke:#3b82f6,color:#fff
    style DOCKER fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style CF fill:#059669,stroke:#10b981,color:#fff
```

Push code to GitHub → containers rebuild automatically → dashboard updates within 60 seconds. Zero manual deployment.

---

## Why This Isn't Just Pattern Matching

Traditional trading bots: "RSI below 30 = always buy." That's pattern matching. Works sometimes, fails catastrophically in others.

This bot is different:

1. **It understands context** — a buy signal during a major news event is completely different from the same signal on a quiet day
2. **9 independent data sources must agree** — no single indicator can trigger a trade
3. **The AI learns from its mistakes** — losing trades get fed back into the LSTM so it learns to avoid those setups
4. **It self-heals** — detects when a strategy stops working and recommends fixes
5. **It knows when to sit out** — at 85% confidence threshold, most scans result in doing nothing. Sitting on your hands when uncertain is one of the hardest things in trading.

---

*For the full technical architecture with detailed Mermaid diagrams, see [ARCHITECTURE.md](ARCHITECTURE.md).*
