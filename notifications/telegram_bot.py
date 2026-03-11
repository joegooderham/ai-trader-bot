"""
notifications/telegram_bot.py — Telegram Notification System
──────────────────────────────────────────────────────────────
Sends all Telegram messages to Joseph.

Messages sent:
  - Trade opened (with reasoning)
  - Trade closed (with P&L)
  - Overnight hold alert (98% rule triggered)
  - Daily end-of-night report
  - Weekly outlook report (Sunday evenings)
  - Health alerts (if bot crashes or goes offline)

All messages are formatted clearly with emoji so you can scan them quickly
on your phone at a glance.
"""

import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from loguru import logger
from datetime import datetime, timezone
from typing import Optional

from bot import config


class TelegramNotifier:
    """Sends formatted messages to your Telegram account."""

    def __init__(self):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.chat_id = config.TELEGRAM_CHAT_ID

    def _send(self, message: str):
        """Send a message (sync wrapper around async Telegram library)."""
        try:
            asyncio.get_event_loop().run_until_complete(
                self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
            )
            logger.debug(f"Telegram message sent: {message[:60]}...")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    # ── Trade Notifications ───────────────────────────────────────────────────

    def trade_opened(
        self,
        pair: str,
        direction: str,
        fill_price: float,
        units: int,
        stop_loss: float,
        take_profit: float,
        confidence_score: float,
        breakdown: dict,
        reasoning: str
    ):
        """Notification sent every time the bot opens a new trade."""

        direction_emoji = "📈" if direction == "BUY" else "📉"
        pair_display = pair.replace("_", "/")

        message = (
            f"*{direction_emoji} TRADE OPENED*\n"
            f"─────────────────────\n"
            f"*Pair:* {pair_display}\n"
            f"*Direction:* {direction}\n"
            f"*Entry Price:* {fill_price}\n"
            f"*Units:* {units:,}\n"
            f"*Stop-Loss:* {stop_loss} _(max loss protected)_\n"
            f"*Take-Profit:* {take_profit}\n"
            f"─────────────────────\n"
            f"*Confidence Score:* {confidence_score:.1f}%\n"
        )

        # Include score breakdown if configured
        if config.SHOW_CONFIDENCE_BREAKDOWN and breakdown:
            message += f"\n*Score Breakdown:*\n"
            message += f"  AI Model: {breakdown.get('lstm_model', 0):.1f}pts\n"
            message += f"  MACD/RSI: {breakdown.get('macd_rsi', 0):.1f}pts\n"
            message += f"  EMA Trend: {breakdown.get('ema_trend', 0):.1f}pts\n"
            message += f"  Bollinger: {breakdown.get('bollinger', 0):.1f}pts\n"
            message += f"  Volume: {breakdown.get('volume', 0):.1f}pts\n"
            message += f"  MCP Context: {breakdown.get('mcp_modifier', 0):+.1f}pts\n"

        # Include plain-English reasoning
        if config.SHOW_AI_REASONING:
            # Trim long reasoning for Telegram readability
            short_reasoning = reasoning.split("\n\nWhy: ")[-1][:300]
            message += f"\n*Why:* _{short_reasoning}_"

        message += f"\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"

        self._send(message)

    def trade_closed(
        self,
        pair: str,
        direction: str,
        close_price: float,
        pl: float,
        reason: str,
        account_balance: float
    ):
        """Notification sent every time the bot closes a trade."""

        pair_display = pair.replace("_", "/")
        is_profit = pl >= 0
        result_emoji = "✅" if is_profit else "❌"
        pl_sign = "+" if is_profit else ""

        message = (
            f"*{result_emoji} TRADE CLOSED*\n"
            f"─────────────────────\n"
            f"*Pair:* {pair_display}\n"
            f"*Closed at:* {close_price}\n"
            f"*Result:* *{pl_sign}£{pl:.2f}*\n"
            f"*Reason:* {reason}\n"
            f"─────────────────────\n"
            f"*Account Balance:* £{account_balance:.2f}\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )

        self._send(message)

    def overnight_hold_alert(
        self,
        pair: str,
        direction: str,
        confidence_score: float,
        current_pl: float,
        reasoning: str
    ):
        """
        Sent when the 98% rule is triggered and a position is held overnight.
        This is rare — you should see this only a few times per month at most.
        """
        pair_display = pair.replace("_", "/")

        message = (
            f"*🌙 OVERNIGHT HOLD — {pair_display}*\n"
            f"─────────────────────\n"
            f"The bot is holding this position overnight.\n\n"
            f"*Confidence Score:* {confidence_score:.1f}% _(above 98% threshold)_\n"
            f"*Direction:* {direction}\n"
            f"*Current P&L:* £{current_pl:.2f}\n"
            f"─────────────────────\n"
            f"*AI Reasoning:* _{reasoning[:300]}_\n\n"
            f"All other positions have been closed as normal.\n"
            f"This position will be reviewed again at 08:00 UTC.\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )

        self._send(message)

    # ── Daily Report ──────────────────────────────────────────────────────────

    def daily_report(
        self,
        date: str,
        total_trades: int,
        wins: int,
        losses: int,
        gross_profit: float,
        gross_loss: float,
        net_pl: float,
        account_balance: float,
        best_pair: Optional[str],
        worst_pair: Optional[str],
        overnight_holds: list,
        system_status: str
    ):
        """
        Sent every night after the last trade closes.
        This is your daily pulse check on how the bot is doing.
        """
        win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0
        pl_emoji = "📈" if net_pl >= 0 else "📉"
        pl_sign = "+" if net_pl >= 0 else ""

        message = (
            f"*{pl_emoji} DAILY REPORT — {date}*\n"
            f"═════════════════════\n"
            f"*Trades Today:* {total_trades} ({wins} wins / {losses} losses)\n"
            f"*Win Rate:* {win_rate}%\n"
            f"─────────────────────\n"
            f"*Gross Profit:* +£{gross_profit:.2f}\n"
            f"*Gross Loss:* -£{gross_loss:.2f}\n"
            f"*Net P&L:* *{pl_sign}£{net_pl:.2f}*\n"
            f"─────────────────────\n"
            f"*Account Balance:* £{account_balance:.2f}\n"
        )

        if best_pair:
            message += f"*Best Pair:* {best_pair.replace('_', '/')}\n"
        if worst_pair:
            message += f"*Worst Pair:* {worst_pair.replace('_', '/')}\n"

        if overnight_holds:
            holds_str = ", ".join([h.replace("_", "/") for h in overnight_holds])
            message += f"\n*🌙 Overnight Holds:* {holds_str}\n"
        else:
            message += f"\n*Overnight Holds:* None _(all positions closed)_\n"

        message += f"*System Status:* {system_status}\n"
        message += f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"

        self._send(message)

    # ── Weekly Report ─────────────────────────────────────────────────────────

    def weekly_report(
        self,
        week_start: str,
        week_end: str,
        total_trades: int,
        overall_pl: float,
        win_rate: float,
        best_pair: Optional[str],
        worst_pair: Optional[str],
        claude_outlook: str
    ):
        """
        Sent every Sunday evening with a full weekly review and
        Claude's outlook for the week ahead.
        """
        pl_sign = "+" if overall_pl >= 0 else ""
        pl_emoji = "📈" if overall_pl >= 0 else "📉"

        message = (
            f"*📊 WEEKLY REPORT*\n"
            f"*{week_start} → {week_end}*\n"
            f"═════════════════════\n"
            f"*Total Trades:* {total_trades}\n"
            f"*Win Rate:* {win_rate:.1f}%\n"
            f"*Net P&L:* *{pl_sign}£{overall_pl:.2f}* {pl_emoji}\n"
            f"─────────────────────\n"
        )

        if best_pair:
            message += f"*Best Pair:* {best_pair.replace('_', '/')}\n"
        if worst_pair:
            message += f"*Worst Pair:* {worst_pair.replace('_', '/')}\n"

        message += (
            f"\n*🤖 AI WEEKLY OUTLOOK:*\n"
            f"─────────────────────\n"
            f"_{claude_outlook[:800]}_\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )

        self._send(message)

    # ── Health Alerts ─────────────────────────────────────────────────────────

    def health_alert(self, issue: str, details: str):
        """
        Sent immediately if something goes wrong with the bot.
        You'll know within seconds if there's a problem.
        """
        message = (
            f"*⚠️ HEALTH ALERT*\n"
            f"─────────────────────\n"
            f"*Issue:* {issue}\n"
            f"*Details:* {details}\n"
            f"\nThe bot may require your attention.\n"
            f"Check the logs: `docker-compose logs -f forex-bot`\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )
        self._send(message)

    def health_recovered(self, issue: str):
        """Sent when the bot recovers from an issue automatically."""
        message = (
            f"*✅ RECOVERED*\n"
            f"The bot has recovered from: {issue}\n"
            f"All systems are operational again.\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )
        self._send(message)

    def startup_message(self):
        """Sent when the bot starts up — confirms it's running."""
        env = config.IG_ENVIRONMENT.upper()
        message = (
            f"*🚀 BOT STARTED*\n"
            f"─────────────────────\n"
            f"AI Trader Bot is now running.\n\n"
            f"*Account:* {env} {'(Demo — no real money)' if env == 'DEMO' else '⚠️ LIVE ACCOUNT'}\n"
            f"*Pairs:* {', '.join([p.replace('_', '/') for p in config.PAIRS])}\n"
            f"*Max Capital:* £{config.MAX_CAPITAL}\n"
            f"*Min Confidence:* {config.MIN_CONFIDENCE_SCORE}%\n"
            f"*Scan Interval:* Every {config.SCAN_INTERVAL_MINUTES} minutes\n"
            f"\nAll systems operational. Watching the markets. 👀\n"
            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
        )
        self._send(message)
