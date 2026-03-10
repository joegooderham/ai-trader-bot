"""
bot/scheduler.py — Main Bot Scheduler
───────────────────────────────────────
This is the entry point for the entire trading bot.
It sets up all scheduled tasks and starts the main loop.

What runs and when:
  Every 15 min  — Scan all pairs for trade signals
  Every  5 min  — Monitor open positions (check P&L, stop-loss status)
  23:45 UTC     — Run EOD evaluation (check 98% overnight hold rule)
  23:59 UTC     — Force-close all remaining positions
  00:05 UTC     — Send daily Telegram report
  Sunday 19:00  — Generate weekly market outlook with Claude AI
  Sunday 20:00  — Send weekly Telegram report

Run with: python -m bot.scheduler
"""

import sys
import asyncio
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from datetime import datetime, timezone

from bot import config
from bot.engine import indicators, confidence
from bot.engine.daily_plan import DailyPlanGenerator
from broker.ig_client import IGClient as OandaClient  # IG drop-in replacement
from notifications.telegram_bot import TelegramNotifier
from notifications.telegram_chat import TelegramChatHandler
from risk.position_sizer import calculate_position_size
from risk.eod_manager import EODManager
from data.storage import TradeStorage
from data.context_writer import ContextWriter
from bot.instance import InstanceManager
import httpx
import threading

# ── Setup logging ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}", level="INFO")
logger.add("/app/logs/forex_bot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="DEBUG")

# ── Initialise all components ─────────────────────────────────────────────────
broker = OandaClient()
notifier = TelegramNotifier()
eod_manager = EODManager(broker, notifier)
storage = TradeStorage()
context_writer = ContextWriter(broker=broker)
instance_manager = InstanceManager(notifier=notifier)
plan_generator = DailyPlanGenerator()
chat_handler = TelegramChatHandler()

MCP_SERVER_URL = "http://mcp-server:8090"


# ── Core Jobs ─────────────────────────────────────────────────────────────────

def scan_markets():
    """
    Main market scan — runs every 15 minutes.

    For each currency pair:
    1. Fetch latest price data
    2. Calculate technical indicators
    3. Get market context from MCP server
    4. Calculate confidence score
    5. Execute trade if confidence >= minimum threshold
    6. Respect capital limit (never exceed max_capital)
    """
    # Respect instance active state — inactive instances skip trading
    if not instance_manager.is_active():
        logger.debug(f"Instance {config.INSTANCE_ID} is not active — skipping scan")
        return

    logger.info("─── Market Scan Started ───")

    # Check how much capital is already deployed
    balance = broker.get_account_balance()
    deployed_capital = broker.get_open_positions_value()
    available_capital = min(config.MAX_CAPITAL - deployed_capital, balance)

    if available_capital <= 0:
        logger.info(f"Capital limit reached (£{config.MAX_CAPITAL} deployed). Skipping scan.")
        return

    open_trades = broker.get_open_trades()
    open_pairs = {t.get("instrument") for t in open_trades}

    if len(open_pairs) >= config.MAX_OPEN_POSITIONS:
        logger.info(f"Maximum open positions ({config.MAX_OPEN_POSITIONS}) reached. Skipping scan.")
        return

    for pair in config.PAIRS:
        # Don't open a second position on a pair we already hold
        if pair in open_pairs:
            logger.debug(f"Already holding {pair} — skipping")
            continue

        try:
            _evaluate_pair(pair, available_capital)
        except Exception as e:
            logger.error(f"Error evaluating {pair}: {e}")

    logger.info("─── Market Scan Complete ───")
    # Update the context file so the Claude app always has fresh data
    context_writer.write()


def _evaluate_pair(pair: str, available_capital: float):
    """
    Evaluate a single currency pair and trade if conditions are right.

    Args:
        pair: e.g. "EUR_USD"
        available_capital: How much we're allowed to deploy
    """
    # Step 1: Get current price data
    candles = broker.get_candles(pair, count=config.LOOKBACK_CANDLES, granularity=config.TIMEFRAME)
    if candles is None or len(candles) < 60:
        logger.warning(f"Insufficient candle data for {pair}")
        return

    # Step 2: Calculate technical indicators
    ind = indicators.calculate(candles)

    # Step 3: Get market context from MCP server
    mcp_context = _get_mcp_context(pair)

    # Step 4: Calculate confidence score with full reasoning
    result = confidence.calculate_confidence(
        pair=pair,
        indicators=ind,
        mcp_context=mcp_context,
        ml_prediction=None  # ML model added in Phase 2
    )

    logger.info(f"{pair}: {result.direction} | Confidence: {result.score:.1f}% | Trade: {result.should_trade}")

    # Step 5: Execute trade if confidence is high enough
    if not result.should_trade:
        return

    # Calculate position size based on risk settings
    stop_distance = ind.atr * config.STOP_LOSS_ATR_MULTIPLIER
    units, stop_loss_price, take_profit_price = calculate_position_size(
        pair=pair,
        direction=result.direction,
        entry_price=ind.current_price,
        atr=ind.atr,
        available_capital=available_capital
    )

    if units <= 0:
        logger.warning(f"Calculated 0 units for {pair} — skipping trade")
        return

    # Step 6: Place the trade
    trade_result = broker.place_trade(
        pair=pair,
        direction=result.direction,
        units=units,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        confidence_score=result.score,
        reasoning=result.reasoning
    )

    if trade_result:
        # Save trade to local storage
        storage.save_trade(trade_result)

        # Send Telegram notification
        notifier.trade_opened(
            pair=pair,
            direction=result.direction,
            fill_price=trade_result["fill_price"],
            units=units,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            confidence_score=result.score,
            breakdown=result.breakdown,
            reasoning=result.reasoning
        )


def monitor_positions():
    """
    Monitor all open positions every 5 minutes.
    Checks for stop-loss hits, take-profit hits, and unusual price movements.
    Updates local trade storage with current P&L.
    """
    open_trades = broker.get_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        trade_id = trade.get("id")
        unrealised_pl = float(trade.get("unrealizedPL", 0))
        logger.debug(f"Position {trade.get('instrument')} | Unrealised P&L: £{unrealised_pl:.2f}")


def eod_evaluation():
    """
    Runs at 23:45 UTC — evaluates all open positions for the 98% overnight rule.
    Positions that qualify are held. All others are closed at 23:59.
    """
    logger.info("Running end-of-day position evaluation (98% rule check)")
    eod_manager.evaluate_overnight_holds()


def force_close_all():
    """
    Runs at 23:59 UTC — closes every remaining open position.
    This is non-negotiable unless a position was granted overnight hold status.
    """
    logger.info("Running end-of-day force close")
    close_results = eod_manager.force_close_non_held_positions()

    if close_results:
        for result in close_results:
            balance = broker.get_account_balance()
            notifier.trade_closed(
                pair=result.get("pair", "Unknown"),
                direction="N/A",
                close_price=result.get("close_price", 0),
                pl=result.get("pl", 0),
                reason="End of day close",
                account_balance=balance
            )


def send_daily_plan():
    """
    Sends tomorrow's trading plan via Telegram after the daily report.
    Powered by Claude AI — gives a strategic view of the next trading day.
    """
    logger.info("Generating tomorrow's trading plan")
    plan = plan_generator.generate()
    notifier._send(plan)


def send_daily_report():
    """
    Runs at 00:05 UTC — compiles and sends the daily Telegram report.
    Covers all trades from the previous day.
    """
    logger.info("Generating daily report")

    from datetime import timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    trades = storage.get_trades_for_date(yesterday)

    if not trades:
        notifier._send(f"📊 *Daily Report — {yesterday}*\nNo trades today.")
        return

    wins = [t for t in trades if t.get("pl", 0) > 0]
    losses = [t for t in trades if t.get("pl", 0) <= 0]
    total_pl = sum(t.get("pl", 0) for t in trades)
    gross_profit = sum(t["pl"] for t in wins)
    gross_loss = abs(sum(t["pl"] for t in losses))

    # Find best and worst performing pairs
    pair_pl = {}
    for t in trades:
        pair = t.get("pair", "")
        pair_pl[pair] = pair_pl.get(pair, 0) + t.get("pl", 0)

    best_pair = max(pair_pl, key=pair_pl.get) if pair_pl else None
    worst_pair = min(pair_pl, key=pair_pl.get) if pair_pl else None

    overnight_holds = storage.get_overnight_holds()
    balance = broker.get_account_balance()

    notifier.daily_report(
        date=yesterday,
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pl=total_pl,
        account_balance=balance,
        best_pair=best_pair,
        worst_pair=worst_pair,
        overnight_holds=overnight_holds,
        system_status="✅ All systems operational"
    )


def send_weekly_report():
    """Runs Sunday 20:00 UTC — fetches Claude's weekly outlook and sends full report."""
    logger.info("Generating weekly report")

    try:
        with httpx.Client(timeout=60) as client:
            response = client.get(f"{MCP_SERVER_URL}/weekly-outlook")
            outlook_data = response.json()
    except Exception as e:
        logger.error(f"Failed to fetch weekly outlook: {e}")
        outlook_data = {"claude_analysis": "Weekly outlook unavailable."}

    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    week_start = (today - timedelta(days=7)).isoformat()
    week_end = today.isoformat()

    trades = storage.get_trades_for_week()
    wins = [t for t in trades if t.get("pl", 0) > 0]
    total_pl = sum(t.get("pl", 0) for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    pair_pl = {}
    for t in trades:
        pair = t.get("pair", "")
        pair_pl[pair] = pair_pl.get(pair, 0) + t.get("pl", 0)

    notifier.weekly_report(
        week_start=week_start,
        week_end=week_end,
        total_trades=len(trades),
        overall_pl=total_pl,
        win_rate=win_rate,
        best_pair=max(pair_pl, key=pair_pl.get) if pair_pl else None,
        worst_pair=min(pair_pl, key=pair_pl.get) if pair_pl else None,
        claude_outlook=outlook_data.get("claude_analysis", "Not available")
    )


def _get_mcp_context(pair: str) -> dict:
    """Fetch market context from the MCP server. Returns empty dict on failure."""
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{MCP_SERVER_URL}/context/{pair}")
            return response.json()
    except Exception as e:
        logger.warning(f"MCP server unavailable for {pair}: {e}")
        return {}


# ── Scheduler Setup ───────────────────────────────────────────────────────────

def main():
    """Start the bot and schedule all jobs."""

    # Validate config before starting
    config.validate()

    # Start instance manager (heartbeat + failover monitoring)
    instance_manager.start()

    # Send startup notification
    notifier.startup_message()

    scheduler = BlockingScheduler(timezone="UTC")

    # Market scan — every 15 minutes
    scheduler.add_job(
        scan_markets,
        "interval",
        minutes=config.SCAN_INTERVAL_MINUTES,
        id="market_scan",
        name="Market Scan"
    )

    # Position monitor — every 5 minutes
    scheduler.add_job(
        monitor_positions,
        "interval",
        minutes=5,
        id="position_monitor",
        name="Position Monitor"
    )

    # Parse EOD times
    eod_eval_h, eod_eval_m = config.EOD_EVALUATION_TIME.split(":")
    eod_close_h, eod_close_m = config.EOD_CLOSE_TIME.split(":")
    report_h, report_m = config.DAILY_REPORT_TIME.split(":")
    weekly_report_h, weekly_report_m = config.WEEKLY_REPORT_TIME.split(":")
    weekly_analysis_h, weekly_analysis_m = config.WEEKLY_ANALYSIS_TIME.split(":")

    # EOD evaluation (98% rule) — daily at 23:45
    scheduler.add_job(
        eod_evaluation,
        CronTrigger(hour=int(eod_eval_h), minute=int(eod_eval_m)),
        id="eod_evaluation",
        name="EOD Evaluation"
    )

    # Force close all positions — daily at 23:59
    scheduler.add_job(
        force_close_all,
        CronTrigger(hour=int(eod_close_h), minute=int(eod_close_m)),
        id="force_close",
        name="Force Close All"
    )

    # Daily report — daily at 00:05
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=int(report_h), minute=int(report_m)),
        id="daily_report",
        name="Daily Report"
    )

    # Weekly analysis — Sunday at 19:00
    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=int(weekly_analysis_h), minute=int(weekly_analysis_m)),
        id="weekly_analysis",
        name="Weekly Analysis"
    )

    # Weekly report — Sunday at 20:00
    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=int(weekly_report_h), minute=int(weekly_report_m)),
        id="weekly_report",
        name="Weekly Report"
    )

    logger.info("📅 Scheduler started with the following jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"   - {job.name}")

    # Daily plan — after daily report (00:10 UTC)
    scheduler.add_job(
        send_daily_plan,
        CronTrigger(hour=0, minute=10),
        id="daily_plan",
        name="Tomorrow's Trading Plan"
    )

    logger.info("📅 Scheduler started with the following jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"   - {job.name}")

    # Start Telegram chat handler in a separate thread
    # This runs alongside the scheduler so you can ask questions any time
    def run_chat_handler():
        logger.info("🤖 Starting Telegram chat interface...")
        chat_app = chat_handler.build_app()
        chat_app.run_polling(drop_pending_updates=True)

    chat_thread = threading.Thread(target=run_chat_handler, daemon=True)
    chat_thread.start()
    logger.info("✅ Telegram chat interface started")

    # Run an immediate scan on startup so you know it's working
    logger.info("Running initial market scan...")
    scan_markets()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        notifier._send("⚠️ *Bot Stopped* — manually stopped by user.")


if __name__ == "__main__":
    main()
