# Setting Up Your Claude Project — Deep Analysis Interface

This guide sets up the Claude app as your deep analysis interface for the trading bot.
Once configured, you can open Claude on any device and ask detailed questions about
your trading history, performance, and strategy — with full context automatically available.

---

## What This Gives You

Every time you open your Claude Project, Claude already knows:

- Your current account balance and open positions
- Today's and this week's trading activity
- Every trade's reasoning (why the bot made each decision)
- All-time performance stats by pair
- Your bot configuration
- Recent health status

You can then ask things like:

> *"Look at my last 30 days — what's my biggest weakness?"*
> *"EUR/USD keeps losing me money. Should I disable it?"*
> *"My win rate dropped this week. What changed?"*
> *"What would you adjust in my config to improve performance?"*
> *"Explain why the bot bought GBP/USD at 2pm yesterday"*

---

## Setup (One-Time, 5 Minutes)

### Step 1 — Find Your Context File

After the bot has been running, a file called `LIVE_CONTEXT.md` is written to your
`data/` folder every 15 minutes. This file contains all your live trading data.

Location on your machine:
```
ai-trader-bot/data/LIVE_CONTEXT.md
```

### Step 2 — Create a Claude Project

1. Open [claude.ai](https://claude.ai) in your browser
2. Click **Projects** in the left sidebar
3. Click **New Project**
4. Name it: **Joseph's Forex Bot**
5. In the project description, paste this:

```
You are Joseph's personal Forex trading assistant. 
You have full access to his trading bot's live data via the attached context file.
Always ground your answers in the actual data. Be direct about weaknesses.
When asked for plans or recommendations, be specific and actionable.
```

### Step 3 — Add Your Context File

**Option A — Manual Upload (Simplest)**
1. Copy the contents of `data/LIVE_CONTEXT.md`
2. In your Claude Project, click **Add Content**
3. Paste the contents
4. Repeat whenever you want fresh data (the file updates every 15 min)

**Option B — GitHub Raw URL (Automatic)**
If your repo is public (or you use a GitHub token):
1. Push `data/LIVE_CONTEXT.md` to GitHub
   ```bash
   git add data/LIVE_CONTEXT.md
   git commit -m "Update live context"
   git push
   ```
2. Get the raw URL:
   `https://raw.githubusercontent.com/YOUR_USERNAME/ai-trader-bot/main/data/LIVE_CONTEXT.md`
3. In your Claude Project settings, add this as a URL source

**Option C — Automate with GitHub Actions (Best)**
Add this to `.github/workflows/sync-context.yml` to auto-commit the context file every 30 minutes:

```yaml
name: Sync Live Context to GitHub

on:
  schedule:
    - cron: '*/30 * * * *'  # Every 30 minutes
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Copy context file from bot
        # This requires the file to be accessible — works if repo is the data store
        run: echo "Context sync - implement based on your setup"
      - name: Commit and push
        run: |
          git config user.name "Forex Bot"
          git config user.email "bot@josephsforexbot.com"
          git add data/LIVE_CONTEXT.md
          git diff --staged --quiet || git commit -m "Auto: update live context $(date -u)"
          git push
```

---

## Example Questions to Ask in Your Claude Project

### Performance Analysis
- *"How has the bot performed overall? Is it profitable?"*
- *"Which pair is giving me the best return on risk?"*
- *"What's my profit factor and what does it mean?"*
- *"Am I improving week over week?"*

### Trade Explanations
- *"Why did the bot sell USD/JPY this morning?"*
- *"The bot lost money on three GBP/USD trades in a row. What went wrong?"*
- *"Explain the last overnight hold — was it the right call?"*

### Strategy & Config
- *"Should I lower my minimum confidence score to get more trades?"*
- *"My win rate is 45% — is that acceptable? What should it be?"*
- *"Which pairs should I remove from my watchlist based on performance?"*
- *"Is my risk-per-trade setting appropriate for my current win rate?"*

### Forward Planning
- *"Based on this week's data, what should I focus on next week?"*
- *"Are there any settings I should change to improve profitability?"*
- *"The bot has been running for 2 weeks. Is it ready for real money yet?"*

---

## Keeping Your Context Fresh

The bot writes `LIVE_CONTEXT.md` automatically every 15 minutes.

For the Claude app to have the latest data:
- **If using manual upload:** Re-paste the file contents periodically
- **If using GitHub:** Commit and push after each trading day
- **If using GitHub Actions:** It syncs automatically every 30 minutes

The context file always includes a timestamp at the top so you can see how fresh it is.

---

## The Difference Between Telegram and Claude App

| | Telegram Bot | Claude App (Project) |
|---|---|---|
| **Best for** | Quick questions on the go | Deep analysis sessions |
| **Examples** | "How today going?" "Any open trades?" | "Analyse my last month and suggest changes" |
| **Data freshness** | Real-time (live API calls) | Up to 30 min behind (file-based) |
| **Conversation memory** | Remembers last 10 exchanges | Full project context always available |
| **Response style** | Short, formatted for phone | Detailed, analytical |
| **Available when** | Bot is running | Any time, any device |

Use Telegram when you're away from your desk and want a quick update.
Use the Claude app when you want to sit down and properly analyse performance.

---

## Privacy Note

The `LIVE_CONTEXT.md` file contains your trading performance data but **no API keys or secrets**.
It is safe to commit to a public GitHub repo.

Never commit your `.env` file — it contains your API keys and is excluded by `.gitignore`.
