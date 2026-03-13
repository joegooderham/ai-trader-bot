"""
notifications/telegram_chat.py — Telegram Conversational Interface
────────────────────────────────────────────────────────────────────
Turns your Telegram bot into a two-way conversation interface.

You can ask it anything about the bot in plain English:
  "How did I do today?"
  "What positions are open right now?"
  "Why did the bot buy EUR/USD at 2pm?"
  "What's my best performing pair this week?"
  "Is everything running ok?"
  "What's the plan for tomorrow?"

How it works:
  1. You send a message to your Telegram bot
  2. This handler receives it
  3. It gathers all relevant live data (trades, positions, health status)
  4. It sends everything to Claude AI with your question
  5. Claude reasons over the data and replies in plain English
  6. You get an intelligent, contextual answer back in Telegram
"""

import asyncio
import json
import sqlite3
import subprocess
from datetime import datetime, timezone, timedelta
from loguru import logger
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ParseMode
import anthropic
import httpx

from bot import config
from data.storage import TradeStorage, DB_PATH
from broker.ig_client import IGClient


class TelegramChatHandler:
    """
    Handles incoming Telegram messages and responds with AI-powered answers.

    This runs as a long-polling Telegram bot handler alongside the
    main trading scheduler. Both share the same bot token.
    """

    def __init__(self):
        self.storage = TradeStorage()
        self.broker = IGClient()
        self.claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.app = None

        # Conversation history per chat — allows follow-up questions
        self._conversation_history = {}

    def build_app(self) -> Application:
        """Build and return the Telegram application."""
        self.app = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .build()
        )

        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_question))
        self.app.add_handler(CommandHandler("today", self.cmd_today))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("health", self.cmd_health))
        self.app.add_handler(CommandHandler("plan", self.cmd_tomorrow_plan))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("fallbacktest", self.cmd_fallback_test))
        self.app.add_handler(CommandHandler("query", self.cmd_query))
        self.app.add_handler(CommandHandler("devops", self.cmd_devops))
        self.app.add_handler(CommandHandler("backtest", self.cmd_backtest))
        self.app.add_handler(CommandHandler("trades", self.cmd_trades))
        self.app.add_handler(CommandHandler("help", self.cmd_help))

        return self.app

    # ── Shortcut Commands ─────────────────────────────────────────────────────

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show available commands and example questions."""
        message = (
            "*🤖 AI Trader Bot — Chat Interface*\n"
            "─────────────────────────────\n"
            "You can ask me anything in plain English, or use these shortcuts:\n\n"
            "*/today* — Today's trades and P&L\n"
            "*/positions* — Currently open positions\n"
            "*/health* — System health status\n"
            "*/plan* — Tomorrow's trading plan\n"
            "*/trades* — Recent trades with index numbers\n"
            "*/stats* — All-time performance stats\n"
            "*/fallbacktest* — Test yfinance backup data source\n"
            "*/query* `<question>` — Query trade database in plain English\n"
            "*/devops* — Today's code changes (git log)\n"
            "*/backtest* — Run LSTM vs indicator-only simulation\n\n"
            "*Or just ask naturally, for example:*\n"
            "_\"How did EUR/USD perform this week?\"_\n"
            "_\"Why did the bot make that last trade?\"_\n"
            "_\"What's my win rate this month?\"_\n"
            "_\"Should I change any settings?\"_\n"
            "_\"What's happening in the markets tomorrow?\"_"
        )
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.handle_question(update, context, override_question="Give me a summary of today's trading activity and P&L")

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.handle_question(update, context, override_question="What positions are currently open right now?")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.handle_question(update, context, override_question="Is the bot healthy and running correctly? Check all systems.")

    async def cmd_tomorrow_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.handle_question(update, context, override_question="What is the trading plan for tomorrow? What should I expect?")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.handle_question(update, context, override_question="Give me a full performance summary with all stats since the bot started.")

    async def cmd_fallback_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Test yfinance fallback data source — hits MCP /test-fallback endpoint."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://mcp-server:8090/test-fallback")
                data = r.json()

            status_emoji = "✅" if data.get("overall_status") == "healthy" else "⚠️"
            message = (
                f"*{status_emoji} YFINANCE FALLBACK TEST*\n"
                f"─────────────────────────────\n"
                f"*Overall:* {data.get('overall_status', 'unknown').upper()}\n\n"
            )

            for pair, result in data.get("pairs", {}).items():
                pair_display = pair.replace("_", "/")
                if result.get("status") == "ok":
                    message += (
                        f"✅ *{pair_display}*: {result['candles_available']} candles | "
                        f"close={result['latest_close']}\n"
                    )
                else:
                    message += f"❌ *{pair_display}*: {result.get('reason', 'failed')}\n"

            message += (
                f"\n─────────────────────────────\n"
                f"If IG goes down, these prices keep the bot scanning.\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Fallback test command failed: {e}")
            await update.message.reply_text(
                "⚠️ Could not reach MCP server to test yfinance fallback.\n"
                f"Error: {str(e)[:200]}"
            )

    async def cmd_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Query the SQLite trade database using natural language.
        Claude translates the question to SQL, executes read-only, returns results."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        question = update.message.text.replace("/query", "", 1).strip()
        if not question:
            await update.message.reply_text(
                "*Usage:* `/query <your question>`\n\n"
                "*Examples:*\n"
                "- `/query how many trades this week`\n"
                "- `/query average P&L on EUR/USD`\n"
                "- `/query best performing pair last 30 days`\n"
                "- `/query show all winning trades above £5`\n"
                "- `/query how many candles stored per pair`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            # Ask Claude to generate SQL from the question
            schema_info = (
                "Tables:\n"
                "- trades: id, trade_id, pair, direction, size, fill_price, close_price, "
                "stop_loss, take_profit, pl, confidence_score, reasoning, status, "
                "opened_at (ISO text), closed_at, close_reason, deal_id, deal_reference, breakdown, created_at\n"
                "- overnight_holds: id, trade_id, pair, score, reasoning, date, created_at\n"
                "- candles: id, pair, timeframe, timestamp, open, high, low, close, volume, source\n"
                "\nPair format: EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD\n"
                "Dates are ISO format text (e.g. '2026-03-12T17:00:00')\n"
            )

            loop = asyncio.get_event_loop()
            sql_response = await loop.run_in_executor(
                None,
                lambda: self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=500,
                    system=(
                        "You are a SQL query generator. Given a natural language question about "
                        "trading data, generate a single READ-ONLY SQLite query. "
                        "Return ONLY the SQL query, nothing else. No markdown, no explanation. "
                        "Never use DELETE, UPDATE, INSERT, DROP, ALTER, or CREATE. "
                        "Only SELECT queries are allowed.\n\n" + schema_info
                    ),
                    messages=[{"role": "user", "content": question}]
                )
            )

            sql = sql_response.content[0].text.strip()

            # Safety: only allow SELECT queries
            sql_upper = sql.upper().strip()
            if not sql_upper.startswith("SELECT"):
                await update.message.reply_text("⚠️ Only SELECT queries are allowed.")
                return

            # Execute the query read-only
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql).fetchall()
            finally:
                conn.close()

            if not rows:
                await update.message.reply_text(
                    f"*Query:* `{sql[:200]}`\n\nNo results found.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            # Format results — ask Claude to summarise
            results_text = json.dumps([dict(r) for r in rows[:50]], default=str, indent=2)

            summary_response = await loop.run_in_executor(
                None,
                lambda: self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=800,
                    system=(
                        "Format these SQL query results for a Telegram message. "
                        "Be concise. Use *bold* for key numbers. Use emojis sparingly. "
                        "Don't use markdown headers (##). If it's a table, use aligned text. "
                        "Max 800 words."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Question: {question}\nSQL: {sql}\nResults ({len(rows)} rows):\n{results_text}"
                    }]
                )
            )

            message = (
                f"*🔍 Database Query*\n"
                f"─────────────────────\n"
                f"*Q:* _{question}_\n"
                f"*SQL:* `{sql[:150]}`\n"
                f"*Rows:* {len(rows)}\n\n"
                f"{summary_response.content[0].text}"
            )

            if len(message) > 4096:
                message = message[:4090] + "..."

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Query command failed: {e}")
            await update.message.reply_text(
                f"⚠️ Query failed: {str(e)[:300]}"
            )

    async def cmd_devops(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show today's git commits — what code changes were made."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            result = subprocess.run(
                ["git", "log", f"--since={today}", "--format=%h %s (%ar)", "--no-merges"],
                capture_output=True, text=True, timeout=10,
                cwd="/app"
            )

            commits = result.stdout.strip()
            if not commits:
                await update.message.reply_text("No code changes today.")
                return

            commit_lines = commits.split("\n")
            message = (
                f"*🛠 Dev Log — {today}*\n"
                f"─────────────────────\n"
                f"*{len(commit_lines)} commits today:*\n\n"
            )

            for line in commit_lines[:20]:
                message += f"• `{line}`\n"

            if len(commit_lines) > 20:
                message += f"\n_...and {len(commit_lines) - 20} more_"

            message += f"\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Devops command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch git log: {str(e)[:200]}")

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List recent trades with their index numbers for reference."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, pair, direction, status, pl, confidence_score, opened_at "
                    "FROM trades ORDER BY id DESC LIMIT 20"
                ).fetchall()
            finally:
                conn.close()

            if not rows:
                await update.message.reply_text("No trades recorded yet.")
                return

            message = (
                "*📋 TRADE LOG*\n"
                "─────────────────────\n"
            )

            for r in rows:
                pair = (r["pair"] or "").replace("_", "/")
                direction = r["direction"] or "?"
                status = r["status"] or "?"
                pl = r["pl"]
                score = r["confidence_score"]
                opened = r["opened_at"] or ""

                # Format P&L
                if pl is not None:
                    pl_str = f"{'+'if pl >= 0 else ''}£{pl:.2f}"
                    result_emoji = "✅" if pl >= 0 else "❌"
                else:
                    pl_str = "open"
                    result_emoji = "⏳"

                # Short date
                date_str = opened[:16].replace("T", " ") if opened else ""

                message += (
                    f"{result_emoji} *#{r['id']}* {pair} {direction} | "
                    f"{pl_str} | {score:.0f}% | {date_str}\n"
                )

            message += (
                f"\n_Showing last {len(rows)} trades_\n"
                f"_Use /query for detailed lookups_\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Trades command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch trades: {str(e)[:200]}")

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run LSTM backtest against historical data and report results."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await update.message.reply_text("Running backtest — this may take a minute...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            import time as _time
            start = _time.time()

            from bot.engine.lstm.backtest import BacktestEngine
            engine = BacktestEngine()
            results = engine.run_all_pairs()
            report = engine.format_report(results)

            duration = _time.time() - start
            report += f"\n\nCompleted in {duration:.1f}s"

            await update.message.reply_text(report, parse_mode=None)

        except Exception as e:
            logger.error(f"Backtest command failed: {e}")
            await update.message.reply_text(f"Backtest failed: {str(e)[:300]}")

    # ── Main Question Handler ─────────────────────────────────────────────────

    async def handle_question(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        override_question: str = None
    ):
        """Main handler — called for every message you send to the bot."""
        chat_id = str(update.effective_chat.id)
        question = override_question or update.message.text

        # Only respond to your own chat (security)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            logger.warning(f"Ignoring message from unknown chat ID: {chat_id}")
            return

        logger.info(f"Chat question received: {question[:80]}")

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            bot_data = await self._gather_bot_data()

            if chat_id not in self._conversation_history:
                self._conversation_history[chat_id] = []

            self._conversation_history[chat_id].append({
                "role": "user",
                "content": question
            })

            response = await self._ask_claude(
                question=question,
                bot_data=bot_data,
                conversation_history=self._conversation_history[chat_id]
            )

            self._conversation_history[chat_id].append({
                "role": "assistant",
                "content": response
            })

            # Keep last 10 exchanges only
            if len(self._conversation_history[chat_id]) > 20:
                self._conversation_history[chat_id] = self._conversation_history[chat_id][-20:]

            if len(response) <= 4096:
                await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
            else:
                chunks = _split_message(response, 4096)
                for chunk in chunks:
                    await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
                    await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(f"Error handling chat question: {e}")
            await update.message.reply_text(
                "⚠️ Sorry, I had trouble getting that information. "
                "Check `docker logs ai-trader-bot` for details."
            )

    # ── Data Gathering ────────────────────────────────────────────────────────

    async def _gather_bot_data(self) -> dict:
        """Collect all live and historical data from the bot for Claude to reason over."""
        data = {}
        now = datetime.now(timezone.utc)

        # ── Account & Live Positions ──────────────────────────────────────────
        try:
            data["account_balance"] = self.broker.get_account_balance()
            open_trades = self.broker.get_open_trades()
            data["open_positions"] = []
            for t in open_trades:
                data["open_positions"].append({
                    "pair":         t.get("instrument", "").replace("_", "/"),
                    "direction":    t.get("direction", ""),
                    "units":        t.get("dealSize", 0),
                    "open_price":   float(t.get("price", 0)),
                    "unrealised_pl": float(t.get("unrealizedPL", 0)),
                    "opened_at":    t.get("openTime", ""),
                })
        except Exception as e:
            data["account_error"] = str(e)
            data["open_positions"] = []

        # ── Today's Trades ────────────────────────────────────────────────────
        today = now.strftime("%Y-%m-%d")
        today_trades = self.storage.get_trades_for_date(today)
        data["today"] = {
            "date":          today,
            "trades":        today_trades,
            "total_trades":  len(today_trades),
            "wins":          len([t for t in today_trades if (t.get("pl") or 0) > 0]),
            "losses":        len([t for t in today_trades if t.get("pl") is not None and (t.get("pl") or 0) <= 0]),
            "net_pl":        round(sum(t.get("pl") or 0 for t in today_trades), 2),
            "pairs_traded":  list(set(t.get("pair", "") for t in today_trades)),
        }

        # ── This Week's Trades ────────────────────────────────────────────────
        week_trades = self.storage.get_trades_for_week()
        pair_pl_week = {}
        for t in week_trades:
            pair = t.get("pair", "Unknown")
            pair_pl_week[pair] = round(pair_pl_week.get(pair, 0) + (t.get("pl") or 0), 2)

        data["this_week"] = {
            "total_trades": len(week_trades),
            "net_pl":       round(sum(t.get("pl") or 0 for t in week_trades), 2),
            "win_rate":     round(
                len([t for t in week_trades if (t.get("pl") or 0) > 0]) / len(week_trades) * 100, 1
            ) if week_trades else 0,
            "pl_by_pair":   pair_pl_week,
            "best_pair":    max(pair_pl_week, key=pair_pl_week.get) if pair_pl_week else None,
            "worst_pair":   min(pair_pl_week, key=pair_pl_week.get) if pair_pl_week else None,
        }

        # ── All-Time Stats ────────────────────────────────────────────────────
        data["all_time_stats"] = self.storage.get_summary_stats()

        # ── Last 5 Trades ─────────────────────────────────────────────────────
        all_trades = self.storage.get_all_trades()
        data["recent_trades"] = all_trades[-5:] if all_trades else []

        # ── System Health ─────────────────────────────────────────────────────
        data["system_health"] = await self._check_health()

        # ── Config Summary ────────────────────────────────────────────────────
        data["bot_config"] = {
            "environment":          config.IG_ENVIRONMENT,
            "max_capital":          config.MAX_CAPITAL,
            "pairs_trading":        config.PAIRS,
            "min_confidence":       config.MIN_CONFIDENCE_SCORE,
            "scan_interval_minutes": config.SCAN_INTERVAL_MINUTES,
            "max_open_positions":   config.MAX_OPEN_POSITIONS,
        }

        # ── Market Outlook (from MCP server) ──────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get("http://mcp-server:8090/weekly-outlook")
                data["market_outlook"] = response.json().get("claude_analysis", "Not available")
        except Exception:
            data["market_outlook"] = "MCP server not available"

        data["current_time_utc"] = now.isoformat()

        return data

    async def _check_health(self) -> dict:
        """Quick health check on all services."""
        health = {}

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("http://mcp-server:8090/health")
                health["mcp_server"] = "✅ Online" if r.status_code == 200 else "❌ Error"
        except Exception:
            health["mcp_server"] = "❌ Offline"

        try:
            balance = self.broker.get_account_balance()
            health["ig_api"] = f"✅ Connected (Balance: £{balance:.2f})"
        except Exception:
            health["ig_api"] = "❌ Cannot connect"

        health["last_checked"] = datetime.now(timezone.utc).strftime("%H:%M UTC")
        return health

    # ── Claude AI Integration ─────────────────────────────────────────────────

    async def _ask_claude(
        self,
        question: str,
        bot_data: dict,
        conversation_history: list
    ) -> str:
        """Send the question and all bot data to Claude for an intelligent answer."""
        system_prompt = f"""You are the AI assistant for Joseph's personal Forex trading bot.

Joseph can ask you anything about his bot's trading activity, performance, and status.
You have access to all live and historical trading data provided below.

Your job is to:
1. Answer his question clearly and helpfully using the data provided
2. Be concise but complete — this is a Telegram chat, not an essay
3. Use simple language — no jargon unless Joseph uses it first
4. Flag anything concerning (unusual losses, system issues, risky patterns)
5. When relevant, suggest actionable improvements to config or strategy

Formatting rules for Telegram:
- Use *bold* for important numbers and headers
- Use emojis sparingly but helpfully (✅ ❌ 📈 📉 ⚠️)
- Keep responses under 800 words unless deep analysis is requested
- Never use markdown headers (##) — Telegram doesn't render them
- Use plain dashes for lists

Current bot data:
{json.dumps(bot_data, indent=2, default=str)}
"""

        messages = []
        for msg in conversation_history[:-1]:
            messages.append(msg)
        messages.append({"role": "user", "content": question})

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=1000,
                    system=system_prompt,
                    messages=messages
                )
            )
            return response.content[0].text

        except Exception as e:
            logger.error(f"Claude API error in chat handler: {e}")
            return (
                "⚠️ I couldn't reach Claude AI to answer that right now.\n"
                "This might be a temporary API issue. Try again in a moment."
            )


# ── Utility ───────────────────────────────────────────────────────────────────

def _split_message(text: str, max_length: int) -> list:
    """Split a long message into chunks at paragraph boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= max_length:
            current += paragraph + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph + "\n\n"

    if current:
        chunks.append(current.strip())

    return chunks