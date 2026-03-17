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

        # Notifier for dispatching formal trade-closed notifications
        # so all trading activity appears in the Telegram trading channel
        from notifications.telegram_bot import TelegramNotifier
        self.notifier = TelegramNotifier()

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
        self.app.add_handler(CommandHandler("closeall", self.cmd_closeall))
        self.app.add_handler(CommandHandler("close", self.cmd_close))
        self.app.add_handler(CommandHandler("pause", self.cmd_pause))
        self.app.add_handler(CommandHandler("resume", self.cmd_resume))
        self.app.add_handler(CommandHandler("datastatus", self.cmd_datastatus))
        self.app.add_handler(CommandHandler("accuracy", self.cmd_accuracy))
        self.app.add_handler(CommandHandler("model", self.cmd_model))
        self.app.add_handler(CommandHandler("drift", self.cmd_drift))
        self.app.add_handler(CommandHandler("performance", self.cmd_performance))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        # ── New Trade Command Handlers ────────────────────────────────────────
        self.app.add_handler(CommandHandler("closepair", self.cmd_close_pair))
        self.app.add_handler(CommandHandler("closeprofitable", self.cmd_close_profitable))
        self.app.add_handler(CommandHandler("closelosing", self.cmd_close_losing))
        self.app.add_handler(CommandHandler("balance", self.cmd_balance))
        self.app.add_handler(CommandHandler("pltoday", self.cmd_pl_today))
        self.app.add_handler(CommandHandler("plweek", self.cmd_pl_week))
        self.app.add_handler(CommandHandler("history", self.cmd_history))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("report", self.cmd_report))
        self.app.add_handler(CommandHandler("setconfidence", self.cmd_set_confidence))
        self.app.add_handler(CommandHandler("setrisk", self.cmd_set_risk))
        self.app.add_handler(CommandHandler("settings", self.cmd_settings))
        self.app.add_handler(CommandHandler("deploy", self.cmd_deploy))
        self.app.add_handler(CommandHandler("deploystatus", self.cmd_deploy_status))
        self.app.add_handler(CommandHandler("integrity", self.cmd_integrity))
        self.app.add_handler(CommandHandler("action", self.cmd_action))
        self.app.add_handler(CommandHandler("discuss", self.cmd_discuss))

        return self.app

    # ── Shortcut Commands ─────────────────────────────────────────────────────

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show available commands and example questions."""
        message = (
            "*🤖 AI Trader Bot — Command List*\n"
            "═════════════════════\n\n"
            "*📊 Positions & Account:*\n"
            "  /positions — Open positions with P&L\n"
            "  /balance — Account funds & equity\n"
            "  /pltoday — Today's realised + unrealised P&L\n"
            "  /plweek — This week's running total\n"
            "  /history — Last 10 closed trades\n"
            "  /trades — Recent trades with index numbers\n\n"
            "*🔒 Close Commands:*\n"
            "  /close `<#>` — Close trade by number\n"
            "  /closeall — Close all positions\n"
            "  /closepair `EURUSD` — Close a specific pair\n"
            "  /closeprofitable — Close all winning positions\n"
            "  /closelosing — Close all losing positions\n\n"
            "*🤖 Bot Control:*\n"
            "  /pause — Stop opening new trades\n"
            "  /resume — Re-enable trading\n"
            "  /status — Bot health & services\n"
            "  /report — Trigger daily report now\n\n"
            "*⚙️ Strategy:*\n"
            "  /setconfidence `50` — Set min confidence %\n"
            "  /setrisk `2` — Set risk per trade %\n"
            "  /settings — Show all current settings\n\n"
            "*🚀 Deploy:*\n"
            "  /deploy — Trigger CI/CD deployment\n"
            "  /deploystatus — Last 5 deploy runs\n\n"
            "*📈 Analytics:*\n"
            "  /accuracy — LSTM prediction accuracy (7d)\n"
            "  /model — LSTM model info & last retrain\n"
            "  /drift — Model drift detection status\n"
            "  /performance — LSTM performance metrics\n"
            "  /integrity — Profit integrity check\n"
            "  /action `<#>` — Apply integrity recommendation\n"
            "  /discuss `<#>` — Discuss a recommendation\n\n"
            "*🔧 Tools:*\n"
            "  /today — Today's summary\n"
            "  /health — System health\n"
            "  /plan — Tomorrow's trading plan\n"
            "  /stats — All-time performance\n"
            "  /datastatus — IG vs yfinance status\n"
            "  /query `<question>` — Query database\n"
            "  /devops — Today's git commits\n"
            "  /backtest — Run LSTM backtest\n"
            "  /fallbacktest — Test yfinance backup\n\n"
            "*💬 Or just ask naturally:*\n"
            '_"How did EUR/USD do this week?"_\n'
            '_"Why did the bot take that last trade?"_\n'
            '_"What\'s my win rate?"_'
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

    async def cmd_datastatus(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show which pairs are using IG vs yfinance fallback."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            # Access the scheduler's live broker to get real fallback state
            import bot.scheduler as scheduler
            live_broker = scheduler.broker

            fallback_pairs = live_broker._fallback_alerted
            all_pairs = config.PAIRS

            if not fallback_pairs:
                message = (
                    "*✅ DATA SOURCE STATUS*\n"
                    "─────────────────────────────\n"
                    "All pairs using *IG live data* — no fallbacks active.\n"
                )
            else:
                ig_pairs = [p for p in all_pairs if p not in fallback_pairs]
                message = (
                    "*🔄 DATA SOURCE STATUS*\n"
                    "─────────────────────────────\n"
                )
                for p in all_pairs:
                    pair_display = p.replace("_", "/")
                    if p in fallback_pairs:
                        message += f"🟡 *{pair_display}*: yfinance (~15 min delay)\n"
                    else:
                        message += f"🟢 *{pair_display}*: IG live\n"

                message += (
                    f"\n─────────────────────────────\n"
                    f"*{len(fallback_pairs)}/{len(all_pairs)}* pairs on yfinance fallback.\n"
                    f"IG demo data allowance resets weekly (usually Sun/Mon).\n"
                    f"Bot continues scanning normally on yfinance data."
                )

            message += f"\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Data status command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch data source status: {str(e)[:200]}")

    # ── Analytics Commands (Phase 3) ──────────────────────────────────────────

    async def cmd_accuracy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show rolling LSTM prediction accuracy."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://mcp-server:8090/analytics/accuracy?window=7d")
                data = r.json()

            overall = data.get("overall", {})
            acc = overall.get("accuracy", 0)
            total = overall.get("total", 0)
            emoji = "✅" if acc >= 50 else "⚠️"

            message = (
                f"*{emoji} LSTM PREDICTION ACCURACY*\n"
                f"─────────────────────────────\n"
                f"*Last 7 Days:* {acc}% ({total} predictions resolved)\n"
                f"*BUY accuracy:* {overall.get('buy_accuracy', 0)}%\n"
                f"*SELL accuracy:* {overall.get('sell_accuracy', 0)}%\n"
            )

            by_pair = data.get("by_pair", {})
            if by_pair:
                message += "\n*By Pair (7d):*\n"
                for pair, pair_acc in by_pair.items():
                    if pair_acc.get("total", 0) > 0:
                        p = pair.replace("_", "/")
                        message += f"  {p}: {pair_acc['accuracy']}% ({pair_acc['total']} predictions)\n"

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Accuracy command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch accuracy: {str(e)[:200]}")

    async def cmd_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current LSTM model info and last retrain details."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://mcp-server:8090/analytics/model")
                data = r.json()

            model = data.get("current_model", {})
            if not model:
                await update.message.reply_text("No model training data yet.")
                return

            message = (
                f"*🧠 LSTM MODEL INFO*\n"
                f"─────────────────────────────\n"
                f"*Version:* {model.get('model_version', '?')}\n"
                f"*Last trained:* {(model.get('timestamp') or '?')[:16]}\n"
                f"*Val accuracy:* {(model.get('val_accuracy') or 0) * 100:.1f}%\n"
                f"*Val loss:* {model.get('val_loss', '?')}\n"
                f"*Train accuracy:* {(model.get('train_accuracy') or 0) * 100:.1f}%\n"
                f"*Epochs:* {model.get('epochs_trained', '?')}\n"
                f"*Features:* {model.get('feature_count', '?')}\n"
                f"*Architecture:* {model.get('num_layers', '?')} layers, {model.get('hidden_size', '?')} hidden\n"
                f"*Training time:* {model.get('training_duration_seconds', '?')}s\n"
                f"*Samples:* {model.get('train_samples', '?')} train, {model.get('val_samples', '?')} val\n"
            )

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Model command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch model info: {str(e)[:200]}")

    async def cmd_drift(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show model drift detection status."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://mcp-server:8090/analytics/drift")
                data = r.json()

            status = data.get("status", "unknown")
            emoji = {"ok": "✅", "drift": "⚠️", "insufficient_data": "📊"}.get(status, "❓")

            message = (
                f"*{emoji} MODEL DRIFT STATUS*\n"
                f"─────────────────────────────\n"
                f"*Status:* {status.upper()}\n"
                f"*Training accuracy:* {data.get('training_accuracy', 0):.1f}%\n"
                f"*Live accuracy (24h):* {data.get('rolling_accuracy_24h', 0):.1f}%\n"
                f"*Live accuracy (7d):* {data.get('rolling_accuracy_7d', 0):.1f}%\n"
                f"*Drift delta:* {data.get('drift_delta', 0):.1f}%\n"
                f"*Predictions resolved (24h):* {data.get('predictions_resolved_24h', 0)}\n\n"
                f"_{data.get('message', '')}_\n"
            )

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Drift command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch drift status: {str(e)[:200]}")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show key LSTM performance metrics."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://mcp-server:8090/analytics/performance?window=7d")
                data = r.json()

            acc = data.get("accuracy", {})
            edge = data.get("lstm_edge")
            trend = data.get("accuracy_trend")
            agreement = data.get("lstm_indicator_agreement")

            message = (
                f"*📊 LSTM PERFORMANCE (7d)*\n"
                f"─────────────────────────────\n"
                f"*Prediction accuracy:* {acc.get('accuracy', 0)}% ({acc.get('total', 0)} resolved)\n"
            )

            if edge is not None:
                edge_emoji = "📈" if edge > 0 else "📉"
                message += f"*LSTM edge:* {edge:+.1f}pp {edge_emoji}\n"

            if agreement is not None:
                message += f"*LSTM-indicator agreement:* {agreement:.0f}%\n"

            if trend is not None:
                trend_emoji = "📈" if trend > 0 else "📉" if trend < 0 else "➡️"
                message += f"*Week-over-week trend:* {trend:+.1f}% {trend_emoji}\n"

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Performance command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch performance: {str(e)[:200]}")

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

    async def cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close all open positions immediately."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            results = self.broker.close_all_positions()

            if not results:
                await update.message.reply_text("No open positions to close.")
                return

            # Persist each close to SQLite and dispatch trade-closed notifications
            try:
                balance = self.broker.get_account_balance()
            except Exception:
                balance = 0

            for r in results:
                deal_id = r.get("deal_id")
                if deal_id:
                    self.storage.update_trade(deal_id, {
                        "close_price": r.get("close_price"),
                        "pl": r.get("pl", 0),
                        "closed_at": r.get("closed_at", datetime.now(timezone.utc).isoformat()),
                        "close_reason": "Manual close all",
                        "status": "CLOSED",
                    })
                    trade_num = self.storage.get_trade_number(deal_id)
                    self.notifier.trade_closed(
                        pair=r.get("pair", "Unknown"),
                        direction="N/A",
                        close_price=r.get("close_price", 0),
                        pl=r.get("pl", 0),
                        reason="Manual close all via /closeall",
                        account_balance=balance,
                        trade_number=trade_num,
                    )

            total_pl = sum(r.get("pl", 0) for r in results)
            pl_sign = "+" if total_pl >= 0 else ""
            pl_emoji = "✅" if total_pl >= 0 else "❌"

            message = (
                f"*{pl_emoji} ALL POSITIONS CLOSED*\n"
                f"─────────────────────\n"
                f"*Closed:* {len(results)} position(s)\n"
                f"*Total P&L:* *{pl_sign}£{total_pl:.2f}*\n"
                f"─────────────────────\n"
            )

            for r in results:
                pl = r.get("pl", 0)
                emoji = "✅" if pl >= 0 else "❌"
                message += f"{emoji} {r.get('deal_id', '?')}: {'+'if pl >= 0 else ''}£{pl:.2f}\n"

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Closeall command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to close positions: {str(e)[:200]}")

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close a specific trade by its index number. Usage: /close 5"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        args = update.message.text.replace("/close", "", 1).strip().lstrip("#")
        if not args or not args.isdigit():
            await update.message.reply_text(
                "*Usage:* `/close <trade number>`\n\n"
                "*Example:* `/close 5` — closes trade #5\n"
                "Use `/trades` to see trade numbers, or `/positions` for open positions.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        trade_number = int(args)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            # Look up the deal_id from the trade number
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT deal_id, pair, direction, size FROM trades WHERE id = ?",
                    (trade_number,)
                ).fetchone()
            finally:
                conn.close()

            if not row:
                await update.message.reply_text(f"⚠️ Trade #{trade_number} not found in database.")
                return

            deal_id = row["deal_id"]
            pair = (row["pair"] or "").replace("_", "/")
            direction = row["direction"] or "BUY"
            size = float(row["size"] or 1.0)

            # Verify the position is actually still open on IG
            open_trades = self.broker.get_open_trades()
            matching = [t for t in open_trades if t.get("dealId") == deal_id]

            if not matching:
                await update.message.reply_text(
                    f"Trade #{trade_number} ({pair} {direction}) is not currently open on IG.\n"
                    f"It may have already been closed."
                )
                return

            # Use the live size from IG in case it differs from the stored value
            live_trade = matching[0]
            live_size = float(live_trade.get("dealSize", size))
            live_direction = live_trade.get("direction", direction)

            result = self.broker.close_trade(deal_id, live_size, live_direction)

            if result:
                pl = result.get("pl", 0)

                # Persist the close to SQLite so dashboard stays in sync with Telegram
                self.storage.update_trade(deal_id, {
                    "close_price": result.get("close_price"),
                    "pl": pl,
                    "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                    "close_reason": "Manual close",
                    "status": "CLOSED",
                })

                # Dispatch formal trade-closed notification so all activity is visible
                try:
                    balance = self.broker.get_account_balance()
                except Exception:
                    balance = 0
                self.notifier.trade_closed(
                    pair=row["pair"] or "",
                    direction=live_direction,
                    close_price=result.get("close_price", 0),
                    pl=pl,
                    reason="Manual close via /close",
                    account_balance=balance,
                    trade_number=trade_number,
                )

                pl_sign = "+" if pl >= 0 else ""
                emoji = "✅" if pl >= 0 else "❌"
                await update.message.reply_text(
                    f"*{emoji} TRADE #{trade_number} CLOSED*\n"
                    f"─────────────────────\n"
                    f"*Pair:* {pair}\n"
                    f"*Direction:* {live_direction}\n"
                    f"*P&L:* *{pl_sign}£{pl:.2f}*\n"
                    f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(f"⚠️ Failed to close trade #{trade_number}. Check logs.")

        except Exception as e:
            logger.error(f"Close command failed: {e}")
            await update.message.reply_text(f"⚠️ Error closing trade: {str(e)[:200]}")

    async def cmd_close_pair(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close a specific pair's position. Usage: /closepair EURUSD or /closepair EUR/USD"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        raw = update.message.text.replace("/closepair", "", 1).strip().upper()
        if not raw:
            await update.message.reply_text(
                "⚠️ *Usage:* `/closepair EURUSD` or `/closepair EUR/USD`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Normalise: "EUR/USD" → "EUR_USD", "EURUSD" → "EUR_USD"
        pair = raw.replace("/", "_")
        if "_" not in pair and len(pair) == 6:
            pair = pair[:3] + "_" + pair[3:]

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            open_trades = self.broker.get_open_trades()
            matching = [t for t in open_trades if (t.get("pair") or t.get("instrument")) == pair]

            if not matching:
                await update.message.reply_text(
                    f"⚠️ No open position found for {pair.replace('_', '/')}."
                )
                return

            total_pl = 0
            results_text = ""
            for trade in matching:
                deal_id = trade.get("dealId")
                size = float(trade.get("dealSize", 1))
                direction = trade.get("direction", "BUY")
                result = self.broker.close_trade(deal_id, size, direction)

                if result:
                    pl = result.get("pl", 0)
                    total_pl += pl
                    # Persist to SQLite
                    self.storage.update_trade(deal_id, {
                        "close_price": result.get("close_price"),
                        "pl": pl,
                        "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                        "close_reason": f"Manual close ({pair.replace('_', '/')})",
                        "status": "CLOSED",
                    })
                    # Dispatch formal trade-closed notification
                    try:
                        balance = self.broker.get_account_balance()
                    except Exception:
                        balance = 0
                    trade_num = self.storage.get_trade_number(deal_id)
                    self.notifier.trade_closed(
                        pair=pair,
                        direction=direction,
                        close_price=result.get("close_price", 0),
                        pl=pl,
                        reason=f"Manual close via /closepair",
                        account_balance=balance,
                        trade_number=trade_num,
                    )
                    emoji = "✅" if pl >= 0 else "❌"
                    results_text += f"{emoji} {direction} {'+'if pl >= 0 else ''}£{pl:.2f}\n"
                else:
                    results_text += f"⚠️ Failed to close {deal_id}\n"

            pl_emoji = "✅" if total_pl >= 0 else "❌"
            await update.message.reply_text(
                f"*{pl_emoji} {pair.replace('_', '/')} CLOSED*\n"
                f"─────────────────────\n"
                f"{results_text}"
                f"─────────────────────\n"
                f"*Total P&L:* {'+'if total_pl >= 0 else ''}£{total_pl:.2f}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Close pair command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to close {pair.replace('_', '/')}: {str(e)[:200]}")

    async def cmd_close_profitable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close only positions currently in profit."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            open_trades = self.broker.get_open_trades()
            profitable = [t for t in open_trades if float(t.get("unrealizedPL", 0)) > 0]

            if not profitable:
                await update.message.reply_text("📊 No profitable positions to close right now.")
                return

            total_pl = 0
            closed_count = 0
            results_text = ""

            for trade in profitable:
                deal_id = trade.get("dealId")
                pair = (trade.get("pair") or trade.get("instrument", "")).replace("_", "/")
                size = float(trade.get("dealSize", 1))
                direction = trade.get("direction", "BUY")
                result = self.broker.close_trade(deal_id, size, direction)

                if result:
                    pl = result.get("pl", 0)
                    total_pl += pl
                    closed_count += 1
                    self.storage.update_trade(deal_id, {
                        "close_price": result.get("close_price"),
                        "pl": pl,
                        "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                        "close_reason": "Manual close (profitable)",
                        "status": "CLOSED",
                    })
                    # Dispatch formal trade-closed notification
                    try:
                        balance = self.broker.get_account_balance()
                    except Exception:
                        balance = 0
                    raw_pair = trade.get("pair") or trade.get("instrument", "")
                    trade_num = self.storage.get_trade_number(deal_id)
                    self.notifier.trade_closed(
                        pair=raw_pair,
                        direction=direction,
                        close_price=result.get("close_price", 0),
                        pl=pl,
                        reason="Manual close via /closeprofitable",
                        account_balance=balance,
                        trade_number=trade_num,
                    )
                    results_text += f"✅ {pair} {direction}: +£{pl:.2f}\n"
                else:
                    results_text += f"⚠️ {pair}: failed to close\n"

            await update.message.reply_text(
                f"*✅ PROFITABLE POSITIONS CLOSED*\n"
                f"─────────────────────\n"
                f"*Closed:* {closed_count}/{len(profitable)} position(s)\n"
                f"{results_text}"
                f"─────────────────────\n"
                f"*Total Realised P&L:* +£{total_pl:.2f}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Close profitable command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to close profitable positions: {str(e)[:200]}")

    async def cmd_close_losing(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close only positions currently at a loss."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            open_trades = self.broker.get_open_trades()
            losing = [t for t in open_trades if float(t.get("unrealizedPL", 0)) < 0]

            if not losing:
                await update.message.reply_text("📊 No losing positions to close right now.")
                return

            total_pl = 0
            closed_count = 0
            results_text = ""

            for trade in losing:
                deal_id = trade.get("dealId")
                pair = (trade.get("pair") or trade.get("instrument", "")).replace("_", "/")
                size = float(trade.get("dealSize", 1))
                direction = trade.get("direction", "BUY")
                result = self.broker.close_trade(deal_id, size, direction)

                if result:
                    pl = result.get("pl", 0)
                    total_pl += pl
                    closed_count += 1
                    self.storage.update_trade(deal_id, {
                        "close_price": result.get("close_price"),
                        "pl": pl,
                        "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                        "close_reason": "Manual close (losing)",
                        "status": "CLOSED",
                    })
                    # Dispatch formal trade-closed notification
                    try:
                        balance = self.broker.get_account_balance()
                    except Exception:
                        balance = 0
                    raw_pair = trade.get("pair") or trade.get("instrument", "")
                    trade_num = self.storage.get_trade_number(deal_id)
                    self.notifier.trade_closed(
                        pair=raw_pair,
                        direction=direction,
                        close_price=result.get("close_price", 0),
                        pl=pl,
                        reason="Manual close via /closelosing",
                        account_balance=balance,
                        trade_number=trade_num,
                    )
                    results_text += f"❌ {pair} {direction}: £{pl:.2f}\n"
                else:
                    results_text += f"⚠️ {pair}: failed to close\n"

            await update.message.reply_text(
                f"*❌ LOSING POSITIONS CLOSED*\n"
                f"─────────────────────\n"
                f"*Closed:* {closed_count}/{len(losing)} position(s)\n"
                f"{results_text}"
                f"─────────────────────\n"
                f"*Total Realised P&L:* £{total_pl:.2f}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Close losing command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to close losing positions: {str(e)[:200]}")

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pause the bot from opening new trades."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            import bot.scheduler as scheduler
            if scheduler._trading_paused:
                await update.message.reply_text("⏸ Trading is already paused. Use /resume to restart.")
                return

            scheduler._trading_paused = True
            logger.info("Trading PAUSED via Telegram /pause command")
            await update.message.reply_text(
                "*⏸ TRADING PAUSED*\n"
                "─────────────────────\n"
                "The bot will not open any new trades.\n"
                "Existing positions remain open and monitored.\n"
                "Use */resume* to restart trading.\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Pause command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to pause: {str(e)[:200]}")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resume trading after a /pause."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            import bot.scheduler as scheduler
            if not scheduler._trading_paused:
                await update.message.reply_text("▶️ Trading is not paused — already running normally.")
                return

            scheduler._trading_paused = False
            logger.info("Trading RESUMED via Telegram /resume command")
            await update.message.reply_text(
                "*▶️ TRADING RESUMED*\n"
                "─────────────────────\n"
                "The bot will resume scanning for trade signals.\n"
                "Next scan will run at the next 15-minute interval.\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Resume command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to resume: {str(e)[:200]}")

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

    # ── Position & Account Commands ──────────────────────────────────────────

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show account funds, equity, margin used, and available capital."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            summary = self.broker.get_account_summary()
            balance = summary.get("balance", 0)
            deposit = summary.get("deposit", 0)
            pl = summary.get("profit_loss", 0)
            available = summary.get("available", 0)
            currency = summary.get("currency", "GBP")

            # Calculate equity (balance + unrealised P&L)
            equity = balance + pl
            margin_used = deposit

            pl_emoji = "📈" if pl >= 0 else "📉"
            pl_sign = "+" if pl >= 0 else ""

            await update.message.reply_text(
                f"*💰 ACCOUNT SUMMARY*\n"
                f"─────────────────────\n"
                f"*Balance:* £{balance:.2f}\n"
                f"*Equity:* £{equity:.2f}\n"
                f"*Unrealised P&L:* {pl_sign}£{pl:.2f} {pl_emoji}\n"
                f"*Margin Used:* £{margin_used:.2f}\n"
                f"*Available:* £{available:.2f}\n"
                f"*Currency:* {currency}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Balance command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch account balance: {str(e)[:200]}")

    async def cmd_pl_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show today's realised + unrealised P&L."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_trades = self.storage.get_trades_for_date(today)

            # Realised P&L from closed trades today
            closed_trades = [t for t in today_trades if t.get("pl") is not None]
            realised_pl = sum(t.get("pl", 0) for t in closed_trades)
            wins = len([t for t in closed_trades if t.get("pl", 0) > 0])
            losses = len([t for t in closed_trades if t.get("pl", 0) <= 0])

            # Unrealised P&L from open positions
            open_trades = self.broker.get_open_trades()
            unrealised_pl = sum(float(t.get("unrealizedPL", 0)) for t in open_trades)

            total_pl = realised_pl + unrealised_pl
            total_emoji = "📈" if total_pl >= 0 else "📉"

            message = (
                f"*{total_emoji} TODAY'S P&L — {today}*\n"
                f"═════════════════════\n"
                f"*Realised P&L:* {'+'if realised_pl >= 0 else ''}£{realised_pl:.2f}\n"
                f"  📊 {len(closed_trades)} closed trades ({wins}W / {losses}L)\n"
                f"*Unrealised P&L:* {'+'if unrealised_pl >= 0 else ''}£{unrealised_pl:.2f}\n"
                f"  📊 {len(open_trades)} open position(s)\n"
                f"─────────────────────\n"
                f"*Total P&L:* *{'+'if total_pl >= 0 else ''}£{total_pl:.2f}*\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"P&L today command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch today's P&L: {str(e)[:200]}")

    async def cmd_pl_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show this week's running total P&L."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            week_trades = self.storage.get_trades_for_week()
            closed_trades = [t for t in week_trades if t.get("pl") is not None]
            total_pl = sum(t.get("pl", 0) for t in closed_trades)
            wins = len([t for t in closed_trades if t.get("pl", 0) > 0])
            losses = len([t for t in closed_trades if t.get("pl", 0) <= 0])
            win_rate = round(wins / len(closed_trades) * 100, 1) if closed_trades else 0

            # P&L by pair
            pair_pl = {}
            for t in closed_trades:
                pair = (t.get("pair", "Unknown")).replace("_", "/")
                pair_pl[pair] = pair_pl.get(pair, 0) + t.get("pl", 0)

            total_emoji = "📈" if total_pl >= 0 else "📉"

            message = (
                f"*{total_emoji} THIS WEEK'S P&L*\n"
                f"═════════════════════\n"
                f"*Total Trades:* {len(closed_trades)}\n"
                f"*Win Rate:* {win_rate}% ({wins}W / {losses}L)\n"
                f"*Net P&L:* *{'+'if total_pl >= 0 else ''}£{total_pl:.2f}*\n"
            )

            if pair_pl:
                message += f"\n*By Pair:*\n"
                for pair, pl in sorted(pair_pl.items(), key=lambda x: x[1], reverse=True):
                    emoji = "✅" if pl >= 0 else "❌"
                    message += f"  {emoji} {pair}: {'+'if pl >= 0 else ''}£{pl:.2f}\n"

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"P&L week command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch weekly P&L: {str(e)[:200]}")

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show last 10 closed trades with outcome."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, pair, direction, fill_price, close_price, pl, "
                    "confidence_score, opened_at, closed_at, close_reason "
                    "FROM trades WHERE closed_at IS NOT NULL "
                    "ORDER BY closed_at DESC LIMIT 10"
                ).fetchall()
            finally:
                conn.close()

            if not rows:
                await update.message.reply_text("📊 No closed trades yet.")
                return

            message = (
                "*📜 TRADE HISTORY (Last 10)*\n"
                "═════════════════════\n"
            )

            for r in rows:
                pair = (r["pair"] or "").replace("_", "/")
                direction = r["direction"] or "?"
                pl = r["pl"]
                score = r["confidence_score"]
                closed = (r["closed_at"] or "")[:16].replace("T", " ")
                reason = r["close_reason"] or ""

                if pl is not None:
                    emoji = "✅" if pl >= 0 else "❌"
                    pl_str = f"{'+'if pl >= 0 else ''}£{pl:.2f}"
                else:
                    emoji = "❓"
                    pl_str = "N/A"

                # Short reason
                short_reason = reason[:25] + "..." if len(reason) > 25 else reason

                message += (
                    f"{emoji} *#{r['id']}* {pair} {direction} | "
                    f"{pl_str} | {score:.0f}%\n"
                    f"    _{closed}_ | _{short_reason}_\n"
                )

            message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"History command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch trade history: {str(e)[:200]}")

    # ── Bot Control Commands ──────────────────────────────────────────────────

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot health, last scan time, next scan time, services status."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            import bot.scheduler as scheduler

            # Trading state
            paused = scheduler._trading_paused
            circuit_breaker = scheduler._circuit_breaker_until
            paused_str = "⏸ *PAUSED*" if paused else "▶️ Active"
            cb_str = ""
            if circuit_breaker and datetime.now(timezone.utc) < circuit_breaker:
                cb_str = f"\n🚨 *Circuit breaker active until:* {circuit_breaker.strftime('%H:%M UTC')}"

            # Open positions
            open_trades = self.broker.get_open_trades()
            total_upl = sum(float(t.get("unrealizedPL", 0)) for t in open_trades)

            # Service health
            health = await self._check_health()

            message = (
                f"*🤖 BOT STATUS*\n"
                f"═════════════════════\n"
                f"*Trading:* {paused_str}{cb_str}\n"
                f"*Open Positions:* {len(open_trades)}\n"
                f"*Unrealised P&L:* {'+'if total_upl >= 0 else ''}£{total_upl:.2f}\n"
                f"─────────────────────\n"
                f"*Services:*\n"
                f"  IG API: {health.get('ig_api', '❓')}\n"
                f"  MCP Server: {health.get('mcp_server', '❓')}\n"
                f"─────────────────────\n"
                f"*Scan Interval:* Every {config.SCAN_INTERVAL_MINUTES} min\n"
                f"*Min Confidence:* {config.MIN_CONFIDENCE_SCORE}%\n"
                f"*Max Positions:* {config.MAX_OPEN_POSITIONS}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Status command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch status: {str(e)[:200]}")

    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger the daily report immediately on demand."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await update.message.reply_text("📊 Generating daily report...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            import bot.scheduler as scheduler
            # Run the daily report in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, scheduler.send_daily_report)
            await update.message.reply_text("✅ Daily report sent!")
        except Exception as e:
            logger.error(f"Report command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to generate report: {str(e)[:200]}")

    # ── Strategy Commands ─────────────────────────────────────────────────────

    async def cmd_set_confidence(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Adjust the confidence threshold on the fly. Usage: /setconfidence 50"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        args = update.message.text.replace("/setconfidence", "", 1).strip()
        if not args:
            await update.message.reply_text(
                f"*Current confidence threshold:* {config.MIN_CONFIDENCE_SCORE}%\n\n"
                f"*Usage:* `/setconfidence 50` — set minimum to 50%",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            new_value = float(args)
            if new_value < 0 or new_value > 100:
                await update.message.reply_text("⚠️ Confidence must be between 0 and 100.")
                return

            old_value = config.MIN_CONFIDENCE_SCORE
            config.MIN_CONFIDENCE_SCORE = new_value
            logger.info(f"Confidence threshold changed: {old_value}% → {new_value}% (via Telegram)")

            await update.message.reply_text(
                f"*⚙️ CONFIDENCE THRESHOLD UPDATED*\n"
                f"─────────────────────\n"
                f"*Previous:* {old_value}%\n"
                f"*New:* {new_value}%\n\n"
                f"_Change is immediate — no restart needed._\n"
                f"_Note: resets on bot restart. Edit config.yaml for permanent change._\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except ValueError:
            await update.message.reply_text("⚠️ Invalid number. Usage: `/setconfidence 50`", parse_mode=ParseMode.MARKDOWN)

    async def cmd_set_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Adjust the % risk per trade. Usage: /setrisk 2"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        args = update.message.text.replace("/setrisk", "", 1).strip()
        if not args:
            await update.message.reply_text(
                f"*Current risk per trade:* {config.PER_TRADE_RISK_PCT}%\n\n"
                f"*Usage:* `/setrisk 2` — set risk to 2% per trade",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            new_value = float(args)
            if new_value <= 0 or new_value > 10:
                await update.message.reply_text("⚠️ Risk must be between 0.1 and 10%.")
                return

            old_value = config.PER_TRADE_RISK_PCT
            config.PER_TRADE_RISK_PCT = new_value
            logger.info(f"Risk per trade changed: {old_value}% → {new_value}% (via Telegram)")

            await update.message.reply_text(
                f"*⚙️ RISK PER TRADE UPDATED*\n"
                f"─────────────────────\n"
                f"*Previous:* {old_value}%\n"
                f"*New:* {new_value}%\n\n"
                f"_Change is immediate — no restart needed._\n"
                f"_Note: resets on bot restart. Edit config.yaml for permanent change._\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                parse_mode=ParseMode.MARKDOWN
            )

        except ValueError:
            await update.message.reply_text("⚠️ Invalid number. Usage: `/setrisk 2`", parse_mode=ParseMode.MARKDOWN)

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show all current bot settings in one message."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        try:
            import bot.scheduler as scheduler
            paused = scheduler._trading_paused

            shadow_mode = "On" if config.LSTM_SHADOW_MODE else "Off"
            lstm_status = "Enabled" if config.LSTM_ENABLED else "Disabled"
            streaming = "Enabled" if config.ENABLE_STREAMING else "Disabled"

            message = (
                f"*⚙️ BOT SETTINGS*\n"
                f"═════════════════════\n"
                f"*Trading:*\n"
                f"  Status: {'⏸ Paused' if paused else '▶️ Active'}\n"
                f"  Environment: {config.IG_ENVIRONMENT.upper()}\n"
                f"  Max Capital: £{config.MAX_CAPITAL}\n"
                f"  Pairs: {', '.join([p.replace('_','/') for p in config.PAIRS])}\n"
                f"  Timeframe: {config.TIMEFRAME}\n"
                f"  Scan Interval: {config.SCAN_INTERVAL_MINUTES} min\n"
                f"  Max Open Positions: {config.MAX_OPEN_POSITIONS}\n"
                f"─────────────────────\n"
                f"*Confidence:*\n"
                f"  Min to Trade: {config.MIN_CONFIDENCE_SCORE}%\n"
                f"  Overnight Hold: {config.HOLD_OVERNIGHT_THRESHOLD}%\n"
                f"─────────────────────\n"
                f"*Risk:*\n"
                f"  Per Trade Risk: {config.PER_TRADE_RISK_PCT}%\n"
                f"  SL ATR Multiplier: {config.STOP_LOSS_ATR_MULTIPLIER}x\n"
                f"  TP Ratio: {config.TAKE_PROFIT_RATIO}x\n"
                f"  Circuit Breaker: {config.DAILY_LOSS_CIRCUIT_BREAKER_PCT}%\n"
                f"  Correlation Block: {config.CORRELATION_BLOCK_THRESHOLD}\n"
                f"─────────────────────\n"
                f"*LSTM:*\n"
                f"  Status: {lstm_status}\n"
                f"  Shadow Mode: {shadow_mode}\n"
                f"  Retrain Interval: {config.LSTM_RETRAIN_INTERVAL_MIN} min\n"
                f"─────────────────────\n"
                f"*Infrastructure:*\n"
                f"  Streaming: {streaming}\n"
                f"  P&L Alert Threshold: £{config.STREAMING_PL_ALERT_THRESHOLD}\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Settings command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch settings: {str(e)[:200]}")

    # ── Deploy Commands ───────────────────────────────────────────────────────

    async def cmd_deploy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger the GitHub Actions CI/CD workflow via workflow dispatch."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        if not config.GITHUB_PAT:
            await update.message.reply_text(
                "⚠️ GITHUB_PAT not configured. Add it to your secrets to enable deploy."
            )
            return

        await update.message.reply_text("🚀 Triggering deployment...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            # Trigger workflow dispatch via GitHub API
            repo = config.GITHUB_REPO
            url = f"https://api.github.com/repos/{repo}/actions/workflows/ci.yml/dispatches"
            headers = {
                "Authorization": f"token {config.GITHUB_PAT}",
                "Accept": "application/vnd.github.v3+json",
            }
            payload = {"ref": "main"}

            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload, headers=headers)

            if r.status_code == 204:
                await update.message.reply_text(
                    f"*🚀 DEPLOY TRIGGERED*\n"
                    f"─────────────────────\n"
                    f"*Repo:* {repo}\n"
                    f"*Branch:* main\n"
                    f"*Workflow:* ci.yml\n\n"
                    f"⏳ Polling for completion (up to 10 min)...\n"
                    f"[View on GitHub](https://github.com/{repo}/actions)\n"
                    f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )

                # Poll for completion in the background
                asyncio.create_task(self._poll_deploy_status(update, context))
            else:
                await update.message.reply_text(
                    f"⚠️ Deploy trigger failed (HTTP {r.status_code})\n"
                    f"Response: {r.text[:300]}"
                )

        except Exception as e:
            logger.error(f"Deploy command failed: {e}")
            await update.message.reply_text(f"⚠️ Deploy failed: {str(e)[:200]}")

    async def _poll_deploy_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Poll GitHub Actions API every 30s for up to 10 minutes to report deploy outcome."""
        repo = config.GITHUB_REPO
        headers = {
            "Authorization": f"token {config.GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Wait a few seconds for the run to appear
        await asyncio.sleep(5)

        max_polls = 20  # 20 × 30s = 10 minutes
        run_id = None

        for i in range(max_polls):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    # Get the most recent workflow run
                    r = await client.get(
                        f"https://api.github.com/repos/{repo}/actions/runs?per_page=1",
                        headers=headers
                    )
                    runs = r.json().get("workflow_runs", [])

                    if not runs:
                        await asyncio.sleep(30)
                        continue

                    run = runs[0]
                    run_id = run.get("id")
                    status = run.get("status")
                    conclusion = run.get("conclusion")

                    if status == "completed":
                        emoji = "✅" if conclusion == "success" else "❌"
                        run_url = run.get("html_url", "")
                        duration = ""
                        if run.get("created_at") and run.get("updated_at"):
                            try:
                                from datetime import datetime as dt
                                created = dt.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                                updated = dt.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
                                secs = (updated - created).total_seconds()
                                duration = f"\n*Duration:* {int(secs)}s"
                            except Exception:
                                pass

                        await update.message.reply_text(
                            f"*{emoji} DEPLOY {'COMPLETE' if conclusion == 'success' else 'FAILED'}*\n"
                            f"─────────────────────\n"
                            f"*Status:* {conclusion.upper()}{duration}\n"
                            f"[View Run](https://github.com/{repo}/actions/runs/{run_id})\n"
                            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True
                        )
                        return

            except Exception as e:
                logger.debug(f"Deploy poll error: {e}")

            await asyncio.sleep(30)

        # Timed out
        await update.message.reply_text(
            f"⏰ Deploy status polling timed out after 10 minutes.\n"
            f"Check manually: https://github.com/{repo}/actions"
        )

    async def cmd_deploy_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the status of the last GitHub Actions deployment."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        if not config.GITHUB_PAT:
            await update.message.reply_text(
                "⚠️ GITHUB_PAT not configured. Add it to your secrets."
            )
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            repo = config.GITHUB_REPO
            headers = {
                "Authorization": f"token {config.GITHUB_PAT}",
                "Accept": "application/vnd.github.v3+json",
            }

            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{repo}/actions/runs?per_page=5",
                    headers=headers
                )
                runs = r.json().get("workflow_runs", [])

            if not runs:
                await update.message.reply_text("📊 No workflow runs found.")
                return

            message = (
                "*🚀 RECENT DEPLOYMENTS*\n"
                "═════════════════════\n"
            )

            for run in runs:
                status = run.get("status", "?")
                conclusion = run.get("conclusion", "")
                name = run.get("name", "?")
                created = (run.get("created_at") or "")[:16].replace("T", " ")

                if status == "completed":
                    emoji = "✅" if conclusion == "success" else "❌"
                    result = conclusion.upper()
                elif status == "in_progress":
                    emoji = "⏳"
                    result = "IN PROGRESS"
                else:
                    emoji = "⏸"
                    result = status.upper()

                message += f"{emoji} {name} | {result} | {created}\n"

            message += (
                f"\n[View all runs](https://github.com/{repo}/actions)\n"
                f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
            )

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"Deploy status command failed: {e}")
            await update.message.reply_text(f"⚠️ Could not fetch deploy status: {str(e)[:200]}")

    async def _send_safe(self, update: Update, text: str):
        """Send a message with Markdown, falling back to plaintext if parsing fails.
        Splits long messages to stay within Telegram's 4096 char limit."""
        chunks = []
        while text:
            if len(text) <= 4000:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, 4000)
            if split_at == -1:
                split_at = 4000
            chunks.append(text[:split_at])
            text = text[split_at:]

        for chunk in chunks:
            if not chunk.strip():
                continue
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                # Markdown parse failed — send without formatting so the message still arrives
                logger.warning("Markdown parse failed, resending chunk without formatting")
                await update.message.reply_text(chunk)

    async def cmd_integrity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run a full profit integrity check and report results."""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            # Use the shared integrity monitor from the scheduler so pending actions persist
            import bot.scheduler as scheduler
            monitor = scheduler.integrity_monitor
            report = monitor.get_full_report()

            # Send report — fall back to plaintext if Markdown parsing fails
            await self._send_safe(update, report)

        except Exception as e:
            logger.error(f"Integrity command failed: {e}")
            await update.message.reply_text(f"⚠️ Integrity check failed: {str(e)[:200]}")

    async def cmd_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apply an integrity recommendation. Usage: /action 1"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        args = update.message.text.replace("/action", "", 1).strip()
        if not args or not args.isdigit():
            await update.message.reply_text(
                "*Usage:* `/action <number>`\n\n"
                "*Example:* `/action 1` — apply recommendation #1\n"
                "Use `/integrity` to see available actions.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        action_id = int(args)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            import bot.scheduler as scheduler
            monitor = scheduler.integrity_monitor
            result = monitor.apply_action(action_id)
            await self._send_safe(update, result)
        except Exception as e:
            logger.error(f"Action command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to apply action: {str(e)[:200]}")

    async def cmd_discuss(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get detailed explanation of a recommendation. Usage: /discuss 1"""
        chat_id = str(update.effective_chat.id)
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return

        args = update.message.text.replace("/discuss", "", 1).strip()
        if not args or not args.isdigit():
            await update.message.reply_text(
                "*Usage:* `/discuss <number>`\n\n"
                "*Example:* `/discuss 1` — explain recommendation #1 in detail\n"
                "Use `/integrity` to see available actions.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        action_id = int(args)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            import bot.scheduler as scheduler
            monitor = scheduler.integrity_monitor
            description = monitor.describe_action(action_id)
            await self._send_safe(update, description)
        except Exception as e:
            logger.error(f"Discuss command failed: {e}")
            await update.message.reply_text(f"⚠️ Failed to describe action: {str(e)[:200]}")

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