# AI Trader Bot — Architecture & Design

A comprehensive overview of the system for technical and non-technical audiences.

---

## What It Does

An AI-powered forex trading bot that scans 10 currency pairs every 3 hours, uses 14 data sources and a neural network to make trade decisions, and manages risk automatically. Controlled via Telegram and a web dashboard.

---

## System Overview

```mermaid
graph TB
    subgraph Internet["☁️ Internet"]
        IG["🏦 IG Group<br/>Broker API"]
        TG["📱 Telegram<br/>Notifications"]
        CF["🔒 Cloudflare<br/>Access + Tunnel"]
        FRED["📊 FRED<br/>Interest Rates"]
        MFX["📊 Myfxbook<br/>Sentiment"]
        COT["📊 CFTC<br/>Institutional Data"]
        VIX_S["📊 VIX<br/>Fear Index"]
        DXY_S["📊 DXY<br/>Dollar Index"]
        YIELD["📊 Treasury<br/>Yield Spread"]
        FNG["📊 Fear & Greed<br/>Index"]
    end

    subgraph Docker["🐳 Docker Network"]
        BOT["🤖 Trading Bot<br/>(forex-bot)"]
        MCP["🔬 MCP Server<br/>(Analysis Engine)"]
        DASH["📊 Dashboard<br/>(Web UI)"]
        HM["❤️ Health Monitor"]
        TUNNEL["🌐 Cloudflare Tunnel"]
    end

    subgraph Storage["💾 Storage"]
        DB[("SQLite<br/>trader.db")]
        CONFIG["⚙️ config.yaml"]
        LOGS["📝 Logs"]
    end

    USER["👤 Joseph"]

    USER -->|"Telegram commands"| TG
    USER -->|"Web browser"| CF
    CF --> TUNNEL --> DASH

    TG <-->|"Alerts + Commands"| BOT
    BOT <-->|"Trade + Price data"| IG
    BOT -->|"Get context"| MCP
    DASH -->|"Commands (port 8060)"| BOT
    DASH -->|"Analytics"| MCP
    HM -->|"Health checks"| BOT
    HM -->|"Health checks"| MCP

    MCP -->|"Rates"| FRED
    MCP -->|"Sentiment"| MFX
    MCP -->|"Positioning"| COT
    MCP -->|"Volatility"| VIX_S
    MCP -->|"USD strength"| DXY_S
    MCP -->|"Bonds"| YIELD
    MCP -->|"Sentiment"| FNG

    BOT --> DB
    BOT --> CONFIG
    BOT --> LOGS
    DASH -.->|"Read-only"| DB

    style BOT fill:#1e40af,stroke:#3b82f6,color:#fff
    style MCP fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style DASH fill:#059669,stroke:#10b981,color:#fff
    style DB fill:#d97706,stroke:#f59e0b,color:#fff
    style USER fill:#dc2626,stroke:#ef4444,color:#fff
```

---

## How a Trade Decision is Made

Every 3 hours, the bot evaluates each currency pair through this pipeline:

```mermaid
flowchart TD
    START([⏰ Scan Triggered<br/>Every 3 Hours]) --> FETCH[📊 Fetch Price Data<br/>60 candles from IG]
    FETCH --> CACHE{Cached?}
    CACHE -->|Yes| TOP_UP[Top up with 3 new candles]
    CACHE -->|No| FULL[Fetch full 60 candles]
    TOP_UP --> INDICATORS
    FULL --> INDICATORS

    INDICATORS[📐 Calculate Technical Indicators<br/>RSI, MACD, Bollinger, EMA, ATR, Volume] --> LSTM

    LSTM[🧠 LSTM Neural Network<br/>Predicts BUY/SELL/HOLD<br/>25+ features, 30-candle sequence] --> MCP_CTX

    MCP_CTX[🔬 Fetch Market Context<br/>14 data sources from MCP Server] --> CONFIDENCE

    CONFIDENCE[⚖️ Calculate Confidence Score<br/>0-100% weighted composite] --> CHECK{Score ≥ 85%?}

    CHECK -->|No| SKIP[⏭️ Skip — Not Confident Enough]
    CHECK -->|Yes| GUARDS

    GUARDS{Direction Disabled?<br/>Pair Disabled?} -->|Yes| SKIP
    GUARDS -->|No| SIZE

    SIZE[📏 Position Sizing<br/>2% risk per trade<br/>ATR-based stop-loss] --> TRADE

    TRADE[💰 Place Trade on IG<br/>Entry + Stop-Loss + Take-Profit] --> NOTIFY

    NOTIFY[📱 Telegram Alert<br/>+ Scan Log Saved to DB]

    SKIP --> LOG[📝 Log Skip Reason<br/>to Scan Audit]

    style START fill:#1e40af,stroke:#3b82f6,color:#fff
    style CONFIDENCE fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style TRADE fill:#059669,stroke:#10b981,color:#fff
    style SKIP fill:#6b7280,stroke:#9ca3af,color:#fff
```

---

## Confidence Score Breakdown

The confidence score determines whether a trade happens. It's built from multiple independent signals:

```mermaid
pie title Confidence Score Components
    "LSTM Neural Network" : 50
    "MACD + RSI" : 20
    "EMA Trend" : 15
    "Bollinger Bands" : 10
    "Volume" : 5
```

After the base score, **14 MCP context signals** adjust it up or down:

```mermaid
graph LR
    subgraph Boost["🟢 Boost Signals (+points)"]
        A1["News aligned +8"]
        A2["IG contrarian +8"]
        A3["Myfxbook contrarian +5"]
        A4["FRED carry trade +5"]
        A5["COT institutional +5"]
        A6["Good session +5"]
        A7["Low volatility +5"]
        A8["VIX calm +3"]
        A9["DXY aligned +5"]
        A10["Yield spread aligned +3"]
        A11["Fear & Greed aligned +5"]
        A12["FinBERT sentiment +5"]
    end

    subgraph Penalty["🔴 Penalty Signals (-points)"]
        B1["High-impact news -15"]
        B2["Extreme volatility -15"]
        B3["News opposed -10"]
        B4["IG with crowd -10"]
        B5["Correlation risk -10"]
        B6["COT opposed -8"]
        B7["FRED against carry -5"]
        B8["Myfxbook crowd -5"]
        B9["Bad session -5"]
        B10["VIX spike -8"]
        B11["DXY opposed -5"]
        B12["Yield inversion -5"]
        B13["Fear & Greed extreme -5"]
        B14["FinBERT opposed -5"]
    end

    BASE["Base Score<br/>0-100%"] --> Boost --> FINAL
    BASE --> Penalty --> FINAL["Final Score<br/>Must be ≥ 85%"]

    style BASE fill:#1e40af,stroke:#3b82f6,color:#fff
    style FINAL fill:#059669,stroke:#10b981,color:#fff
```

---

## LSTM Neural Network

The AI brain that predicts market direction. Contributes 50% of the confidence score.

```mermaid
graph TD
    subgraph Input["📥 Input: 25+ Features per Candle"]
        F1["Technical: RSI, MACD, Bollinger,<br/>EMA, ATR, Volume (12 features)"]
        F2["Time: Hour + Day encoding (4 features)"]
        F3["Derived: RSI momentum, MACD distance,<br/>EMA cross momentum (2 features)"]
        F4["External: IG sentiment, Myfxbook,<br/>COT, FRED, Volatility, H4 trend (7 features)"]
        F5["New External: VIX, DXY,<br/>Treasury yield, Fear & Greed (4 features)"]
    end

    subgraph Model["🧠 LSTM Model (~119k parameters)"]
        L1["LSTM Layer 1<br/>96 hidden units"]
        L2["LSTM Layer 2<br/>96 hidden units"]
        ATT["Self-Attention Layer<br/>Weights important candles"]
        BN["Batch Normalisation"]
        DROP["Dropout 0.3"]
        OUT["Output: BUY / SELL / HOLD<br/>with probability"]
    end

    subgraph Training["🔄 Continuous Training"]
        T1["Retrains every 4 hours"]
        T2["3-6 months of candle data"]
        T3["Real trade outcomes<br/>fed back (wins 2x weighted)"]
        T4["Hot-swaps live model<br/>No restart needed"]
    end

    F1 & F2 & F3 & F4 & F5 --> SEQ["30-Candle Sequence"]
    SEQ --> L1 --> L2 --> ATT --> BN --> DROP --> OUT

    T1 & T2 & T3 --> T4

    style Model fill:#1e40af,stroke:#3b82f6,color:#fff
    style ATT fill:#7c3aed,stroke:#8b5cf6,color:#fff
```

---

## Risk Management

```mermaid
graph TD
    subgraph PerTrade["🎯 Per-Trade Protection"]
        R1["2% max risk per trade<br/>(£10 on £500 capital)"]
        R2["2.0x ATR stop-loss<br/>(adapts to volatility)"]
        R3["2.0x ATR trailing stop<br/>(locks in profit)"]
        R4["2:1 reward-to-risk ratio"]
    end

    subgraph Portfolio["📊 Portfolio Protection"]
        P1["Correlation blocking<br/>(no doubling similar bets)"]
        P2["Max open positions limit"]
        P3["Daily loss circuit breaker<br/>(10% pause)"]
        P4["Weekly loss auto-pause<br/>(£50 threshold)"]
    end

    subgraph EOD["🌙 End of Day (23:59 UTC)"]
        E1["Close all positions"]
        E2["Exception: hold if ≥65%<br/>confidence AND profitable"]
        E3["Tighten stop to protect<br/>50% of overnight profit"]
    end

    style PerTrade fill:#059669,stroke:#10b981,color:#fff
    style Portfolio fill:#d97706,stroke:#f59e0b,color:#fff
    style EOD fill:#7c3aed,stroke:#8b5cf6,color:#fff
```

---

## Automated Remediation System

The bot monitors its own performance and fixes problems automatically:

```mermaid
stateDiagram-v2
    [*] --> Monitoring: Bot Trading

    Monitoring --> IssueDetected: Integrity Review<br/>(every 3 hours)

    IssueDetected --> Diagnosis: Analyse Root Cause

    Diagnosis --> LosingStreak: >5 consecutive losses
    Diagnosis --> DirectionFail: BUY or SELL <30% win rate
    Diagnosis --> WeeklyDecline: Week-over-week P&L drop
    Diagnosis --> LSTMDrift: Model accuracy <45%
    Diagnosis --> HeavyLoss: Weekly P&L < -£50

    LosingStreak --> Recommend: Smart analysis<br/>(direction? pair? confidence?)
    DirectionFail --> Recommend: Disable that direction
    WeeklyDecline --> Recommend: Defensive adjustments
    LSTMDrift --> Recommend: Enable shadow mode

    HeavyLoss --> AutoPause: 🚨 Immediate pause<br/>(no approval needed)

    Recommend --> TelegramButtons: Send inline buttons<br/>[✅ Approve] [❌ Reject]
    TelegramButtons --> Applied: User approves
    TelegramButtons --> Dismissed: User rejects

    Applied --> Monitoring: Fix applied at runtime<br/>No restart needed
    Dismissed --> Monitoring: Recommendation cleared
    AutoPause --> ManualResume: User sends /resume

    ManualResume --> Monitoring
```

---

## Dashboard

Interactive web dashboard at `aitradefintech.com`, protected by Cloudflare Access (Google OAuth).

```mermaid
graph TB
    subgraph Trading["📈 Trading"]
        O["Overview<br/>P&L, stats, charts"]
        P["Positions<br/>Close buttons, controls"]
        T["Trades<br/>History, pagination"]
        J["Journal<br/>Expandable trade cards"]
        S["Scan Log<br/>Full audit trail"]
        CAL["Calendar<br/>Daily P&L grid"]
    end

    subgraph Analytics["📊 Analytics"]
        LSTM_A["LSTM Analytics<br/>Model, accuracy, drift"]
        HM["Heatmap<br/>Pair × Hour grid"]
        SESS["Sessions<br/>London/NY/Tokyo/Sydney"]
        CORR["Correlations<br/>Pair matrix"]
        RISK["Risk Exposure<br/>Portfolio risk"]
    end

    subgraph Tools["🛠️ Tools"]
        CHAT["AI Chat<br/>Ask Claude anything"]
        MW["Mystic Wolf<br/>What-if simulator"]
        REM["Remediation<br/>Approve/reject fixes"]
        CONF["Config<br/>Live parameter editor"]
    end

    style Trading fill:#1e40af,stroke:#3b82f6,color:#fff
    style Analytics fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style Tools fill:#059669,stroke:#10b981,color:#fff
```

---

## Data Flow — Where Information Lives

```mermaid
flowchart LR
    subgraph Sources["Data Sources"]
        IG["IG Group<br/>(candles, positions)"]
        YF["yfinance<br/>(fallback candles)"]
        FRED_D["FRED<br/>(interest rates)"]
        MFX_D["Myfxbook<br/>(retail sentiment)"]
        COT_D["CFTC<br/>(institutional data)"]
        VIX_D["VIX<br/>(fear index)"]
        DXY_D["DXY<br/>(dollar index)"]
        YIELD_D["Treasury<br/>(yield spread)"]
        FNG_D["Fear & Greed<br/>(market sentiment)"]
        FINBERT_D["FinBERT<br/>(NLP headlines)"]
    end

    subgraph Processing["Processing"]
        BOT_D["Trading Bot<br/>(decisions, execution)"]
        MCP_D["MCP Server<br/>(context, analytics)"]
    end

    subgraph Storage_D["Storage"]
        DB_D[("SQLite<br/>trades, candles,<br/>predictions, scans")]
        YAML["config.yaml<br/>(settings)"]
    end

    subgraph Output["Output"]
        TG_D["Telegram<br/>(alerts, commands)"]
        DASH_D["Dashboard<br/>(visualisation)"]
    end

    IG --> BOT_D
    YF -.->|fallback| BOT_D
    FRED_D & MFX_D & COT_D & VIX_D & DXY_D & YIELD_D & FNG_D & FINBERT_D --> MCP_D
    MCP_D --> BOT_D
    BOT_D --> DB_D
    BOT_D --> TG_D
    DB_D --> DASH_D
    MCP_D --> DASH_D
    YAML --> BOT_D

    style BOT_D fill:#1e40af,stroke:#3b82f6,color:#fff
    style DB_D fill:#d97706,stroke:#f59e0b,color:#fff
```

---

## Infrastructure

```mermaid
graph LR
    subgraph Local["💻 Self-Hosted (Windows PC)"]
        subgraph Docker["🐳 Docker Compose"]
            C1["forex-bot<br/>Trading engine<br/>+ Command API :8060"]
            C2["mcp-server<br/>Analysis engine<br/>:8090"]
            C3["dashboard<br/>Web UI<br/>:8050"]
            C4["health-monitor<br/>Watchdog"]
            C5["cloudflared<br/>Tunnel"]
        end
        VOL["📁 Volumes<br/>data_store, logs, config"]
    end

    subgraph Cloud["☁️ Cloudflare"]
        ACCESS["Access<br/>Google OAuth"]
        DNS["DNS<br/>aitradefintech.com"]
    end

    GH["🐙 GitHub<br/>CI/CD Actions"]

    C5 -->|"Encrypted tunnel"| DNS
    DNS --> ACCESS -->|"Authenticated"| C3
    GH -->|"Push to main<br/>auto-deploys"| Docker

    style C1 fill:#1e40af,stroke:#3b82f6,color:#fff
    style C2 fill:#7c3aed,stroke:#8b5cf6,color:#fff
    style C3 fill:#059669,stroke:#10b981,color:#fff
    style ACCESS fill:#dc2626,stroke:#ef4444,color:#fff
```

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Currency pairs | 10 (EUR/USD, GBP/USD, USD/JPY, + 7 more) |
| Scan frequency | Every 3 hours |
| Confidence threshold | 85% minimum to trade |
| Data sources | 14 (LSTM, technicals, IG sentiment, Myfxbook, COT, FRED, VIX, DXY, Treasury yield, Fear & Greed, FinBERT NLP, calendar, volatility regime, session performance) |
| LSTM features | 25+ per candle (18 base + 7 external signals) |
| Daily profit target | £20 (bank and pause when hit) |
| Position reconciliation | Every 5 minutes |
| Health audit | Twice daily (09:00 + 17:00 UTC) |
| Integrity review | Every 3 hours (aligned with scans) |
| Deep review | Every 6 hours |
| Dashboard pages | 20 |
| Risk per trade | 2% of capital (£10 on £500) |
| Stop-loss | 2.0× ATR (adapts to volatility) |
| Reward:risk | 2:1 minimum |
| Capital | £500 (demo account) |
| Broker | IG Group (demo) |
| Dashboard | aitradefintech.com (Cloudflare Access protected) |
| Hosting | Self-hosted Docker on Windows |
| CI/CD | GitHub Actions → auto-deploy on push to main |

---

## Tech Stack

```mermaid
mindmap
  root((AI Trader Bot))
    Trading
      Python
      APScheduler
      IG Group API
      PyTorch LSTM
    Data
      SQLite
      yfinance
      FRED API
      Myfxbook
      CFTC/Quandl
    Dashboard
      React 18
      Vite + Tailwind
      FastAPI backend
      Recharts
    Infrastructure
      Docker Compose
      GitHub Actions CI/CD
      Cloudflare Tunnel + Access
    Notifications
      Telegram Bot API
      Dual bots (trading + system)
      Inline keyboard buttons
    AI
      Claude Sonnet (chat + analysis)
      LSTM (trade predictions)
      FinBERT (NLP sentiment)
      25+ input features
      Self-attention mechanism
```

---

*Last updated: March 2026*
