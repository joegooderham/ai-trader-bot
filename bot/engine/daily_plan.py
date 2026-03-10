"""
bot/engine/daily_plan.py — Tomorrow's Trading Plan Generator
─────────────────────────────────────────────────────────────
Every evening after the daily close, the bot generates a strategic
plan for the next trading day. This is sent to you via Telegram
and also written into the context file for the Claude app.

The plan considers:
  - Upcoming economic calendar events (what news could move markets)
  - Recent performance patterns (what's been working/not working)
  - Current volatility conditions
  - Session timing recommendations
  - Pairs to focus on vs. pairs to be cautious with

It uses Claude AI to reason over all of this and write a plain-English
plan you'd actually find useful — not a wall of numbers.
"""

import json
from datetime import datetime, timezone, timedelta
from loguru import logger
import anthropic
import httpx

from bot import config
from data.storage import TradeStorage


class DailyPlanGenerator:
    """Generates tomorrow's trading plan using Claude AI."""

    def __init__(self):
        self.storage = TradeStorage()
        self.claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def generate(self) -> str:
        """
        Generate a plain-English trading plan for tomorrow.

        Returns a formatted string suitable for Telegram.
        Called at the end of each trading day, after the daily report.
        """
        logger.info("Generating tomorrow's trading plan")

        # Gather everything Claude needs to write an intelligent plan
        context = self._gather_planning_context()

        # Ask Claude to synthesise this into a strategic plan
        plan = self._ask_claude_for_plan(context)

        return plan

    def _gather_planning_context(self) -> dict:
        """Collect all data needed for the planning analysis."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).strftime("%A %d %B %Y")

        # Recent performance (last 7 days)
        week_trades = self.storage.get_trades_for_week()
        pair_pl = {}
        pair_trades = {}
        pair_wins = {}
        for t in week_trades:
            p = t.get("pair", "Unknown").replace("_", "/")
            pair_pl[p] = round(pair_pl.get(p, 0) + t.get("pl", 0), 2)
            pair_trades[p] = pair_trades.get(p, 0) + 1
            if t.get("pl", 0) > 0:
                pair_wins[p] = pair_wins.get(p, 0) + 1

        pair_stats = {}
        for pair in pair_pl:
            n = pair_trades.get(pair, 1)
            pair_stats[pair] = {
                "net_pl": pair_pl[pair],
                "trades": n,
                "win_rate": round(pair_wins.get(pair, 0) / n * 100, 1)
            }

        # All-time stats for baseline comparison
        all_time = self.storage.get_summary_stats()

        # Fetch economic calendar from MCP server
        calendar_events = []
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get("http://mcp-server:8090/weekly-outlook")
                calendar_events = r.json().get("economic_events", [])
        except Exception:
            calendar_events = []

        return {
            "tomorrow_date": tomorrow,
            "day_of_week": (now + timedelta(days=1)).strftime("%A"),
            "week_performance_by_pair": pair_stats,
            "week_net_pl": round(sum(t.get("pl", 0) for t in week_trades), 2),
            "week_win_rate": round(
                len([t for t in week_trades if t.get("pl", 0) > 0]) /
                len(week_trades) * 100, 1
            ) if week_trades else 0,
            "all_time_stats": all_time,
            "upcoming_economic_events": calendar_events[:10],  # Top 10 events
            "bot_config": {
                "pairs": [p.replace("_", "/") for p in config.PAIRS],
                "min_confidence": config.MIN_CONFIDENCE_SCORE,
                "max_capital": config.MAX_CAPITAL,
                "environment": config.OANDA_ENVIRONMENT,
            }
        }

    def _ask_claude_for_plan(self, context: dict) -> str:
        """
        Ask Claude AI to write tomorrow's trading plan.

        The plan covers:
        1. Overall market mood for tomorrow
        2. Pairs to focus on (based on recent performance)
        3. Pairs to be cautious with
        4. Key economic events to watch
        5. Session timing recommendation
        6. Any config adjustments worth considering
        """
        prompt = f"""You are writing the end-of-day trading plan for Joseph's Forex bot.

Here is all the context you need:
{json.dumps(context, indent=2)}

Write a practical trading plan for tomorrow ({context['tomorrow_date']}).

Structure it exactly like this (use these exact headers):

**📅 Plan for {context['tomorrow_date']}**

**Market Outlook**
[2-3 sentences on what to generally expect tomorrow based on day of week, events, and recent momentum]

**Pairs to Focus On**
[List 2-3 pairs that have been performing well this week and why]

**Pairs to Watch Carefully**
[List any pairs that have been underperforming or have risky events tomorrow]

**Key Events to Watch**
[List the 2-3 most important economic events tomorrow and what impact they could have on specific pairs]

**Session Strategy**
[Which trading sessions look most promising tomorrow and why]

**One Thing to Improve**
[One specific, actionable suggestion based on this week's performance data]

Keep it concise and practical. Joseph reads this on his phone. 
Max 400 words total. Be direct — if a pair is performing badly, say so clearly."""

        try:
            response = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text

        except Exception as e:
            logger.error(f"Claude API error generating daily plan: {e}")
            return (
                f"📅 *Plan for tomorrow*\n\n"
                f"Could not generate AI plan (Claude API error).\n"
                f"Check logs: `docker-compose logs forex-bot`"
            )
