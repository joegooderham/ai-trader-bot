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

import os
import sys
import time
import asyncio
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from datetime import datetime, timezone

from bot import config
from bot.engine import indicators, confidence
from bot.engine.daily_plan import DailyPlanGenerator
from broker.ig_client import IGClient
from notifications.telegram_bot import TelegramNotifier
from notifications.telegram_chat import TelegramChatHandler
from risk.position_sizer import calculate_position_size
from risk.eod_manager import EODManager
from data.storage import TradeStorage
from data.context_writer import ContextWriter
from bot.instance import InstanceManager
from bot.engine.lstm import LSTMPredictor
import httpx

# ── Setup logging ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}", level="INFO")
logger.add("/app/logs/forex_bot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="DEBUG")

# ── Initialise all components ─────────────────────────────────────────────────
broker = IGClient()
notifier = TelegramNotifier()
# Connect notifier to broker so it can send Telegram alerts on yfinance fallback
broker.set_notifier(notifier)
eod_manager = EODManager(broker, notifier)
storage = TradeStorage()
context_writer = ContextWriter(broker=broker)
instance_manager = InstanceManager(notifier=notifier)
plan_generator = DailyPlanGenerator()
chat_handler = TelegramChatHandler()

# LSTM predictor — loads saved model at startup (gracefully returns None if no model yet)
lstm_predictor = LSTMPredictor() if config.LSTM_ENABLED else None

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
    if not instance_manager.is_active():
        logger.debug(f"Instance {config.INSTANCE_ID} is not active — skipping scan")
        return

    logger.info("─── Market Scan Started ───")

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
        if pair in open_pairs:
            logger.debug(f"Already holding {pair} — skipping")
            continue

        try:
            _evaluate_pair(pair, available_capital)
        except Exception as e:
            logger.error(f"Error evaluating {pair}: {e}")

    logger.info("─── Market Scan Complete ───")

    try:
        context_writer.write()
    except Exception as e:
        logger.error(f"Failed to write context file: {e}")


def _evaluate_pair(pair: str, available_capital: float):
    """Evaluate a single currency pair and trade if conditions are right."""
    candles = broker.get_candles(pair, count=config.LOOKBACK_CANDLES, granularity=config.TIMEFRAME)
    if candles is None or len(candles) < 60:
        logger.warning(f"Insufficient candle data for {pair}")
        return

    # Persist live candles to SQLite so the LSTM trains on real broker data
    # INSERT OR IGNORE handles duplicates, so this is safe to call every scan
    try:
        storage.save_candles(pair, config.TIMEFRAME, candles, source="ig_live")
    except Exception as e:
        logger.debug(f"Failed to save live candles for {pair}: {e}")

    ind = indicators.calculate(candles)
    mcp_context = _get_mcp_context(pair)

    # Get LSTM prediction if model is loaded and enabled
    ml_prediction = None
    if lstm_predictor:
        ml_prediction = lstm_predictor.predict(pair, candles)

    if config.LSTM_SHADOW_MODE and ml_prediction:
        # Shadow mode: score WITH and WITHOUT LSTM, log both, but only act on indicator-only
        lstm_result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context, ml_prediction=ml_prediction
        )
        indicator_result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context, ml_prediction=None
        )

        # Log the comparison so we can see if LSTM is adding value
        diff = lstm_result.score - indicator_result.score
        diff_str = f"+{diff:.1f}" if diff >= 0 else f"{diff:.1f}"
        logger.info(
            f"{pair} SHADOW | LSTM: {lstm_result.score:.1f}% {lstm_result.direction} | "
            f"Indicators: {indicator_result.score:.1f}% {indicator_result.direction} | "
            f"Delta: {diff_str}pp | LSTM pred: {ml_prediction}"
        )

        # Use indicator-only result for actual trade decisions until shadow mode is off
        result = indicator_result
    else:
        # Live mode: LSTM score drives real trade decisions (50% weight)
        result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context, ml_prediction=ml_prediction
        )

    logger.info(f"{pair}: {result.direction} | Confidence: {result.score:.1f}% | Trade: {result.should_trade}")

    if not result.should_trade:
        return

    size, stop_loss_price, take_profit_price = calculate_position_size(
        pair=pair,
        direction=result.direction,
        entry_price=ind.current_price,
        atr=ind.atr,
        available_capital=available_capital
    )

    if size <= 0:
        logger.warning(f"Calculated 0 size for {pair} — skipping trade")
        return

    trade_result = broker.place_trade(
        pair=pair,
        direction=result.direction,
        size=size,
        stop_loss=stop_loss_price,
        take_profit=take_profit_price,
    )

    if trade_result:
        storage.save_trade(trade_result)

        notifier.trade_opened(
            pair=pair,
            direction=result.direction,
            fill_price=trade_result["fill_price"],
            units=size,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            confidence_score=result.score,
            breakdown=result.breakdown,
            reasoning=result.reasoning
        )


def monitor_positions():
    """Monitor all open positions every 5 minutes."""
    open_trades = broker.get_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        unrealised_pl = float(trade.get("unrealizedPL", 0))
        logger.debug(f"Position {trade.get('instrument')} | Unrealised P&L: £{unrealised_pl:.2f}")


def eod_evaluation():
    """Runs at 23:45 UTC — evaluates all open positions for the 98% overnight rule."""
    logger.info("Running end-of-day position evaluation (98% rule check)")
    eod_manager.evaluate_overnight_holds()


def force_close_all():
    """Runs at 23:59 UTC — closes every remaining open position."""
    logger.info("Running end-of-day force close")
    # Clear candle cache at day rollover so fresh data loads tomorrow
    broker.clear_candle_cache()
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
    """Sends tomorrow's trading plan via Telegram."""
    logger.info("Generating tomorrow's trading plan")
    plan = plan_generator.generate()
    notifier._send(plan)


def send_daily_report():
    """Runs at 00:05 UTC — compiles and sends the daily Telegram report."""
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


# Track whether a retrain is already running so we don't stack them up
_retrain_lock = threading.Lock()
_retrain_running = False


def retrain_lstm():
    """
    Continuous LSTM retrain — runs on a rolling interval (default 4h).
    Downloads latest data, trains fresh model, reloads predictor.
    Runs in a background thread so it never blocks market scans.

    Training duration is reported in Telegram so we can decide whether
    to tighten the interval towards real-time retraining.
    """
    global _retrain_running

    # Don't stack retrains — if one is already running, skip this cycle
    if not _retrain_lock.acquire(blocking=False):
        logger.info("LSTM retrain already in progress — skipping this cycle")
        return

    try:
        _retrain_running = True
        logger.info("═══ LSTM Retrain Started ═══")

        from bot.engine.lstm.trainer import LSTMTrainer
        trainer = LSTMTrainer()
        metrics = trainer.train(
            epochs=config.LSTM_EPOCHS,
            batch_size=config.LSTM_BATCH_SIZE,
            lr=config.LSTM_LEARNING_RATE,
            patience=config.LSTM_PATIENCE,
        )

        if "error" in metrics:
            logger.error(f"LSTM retrain failed: {metrics['error']}")
            notifier._send(f"⚠️ *LSTM Retrain Failed*\n{metrics['error']}")
            return

        # Reload the predictor with the freshly trained model
        if lstm_predictor:
            lstm_predictor.reload()

        duration = metrics.get("training_duration_human", "?")
        notifier._send(
            f"🧠 *LSTM Model Retrained* ({duration})\n"
            f"Val accuracy: {metrics['val_accuracy']:.1%}\n"
            f"Val loss: {metrics['best_val_loss']:.4f}\n"
            f"Epochs: {metrics['epochs_trained']}\n"
            f"Samples: {metrics['train_samples']} train, {metrics['val_samples']} val\n"
            f"{'Extended data: ' + metrics['extended_period'] if metrics.get('data_extended') else ''}"
        )
        logger.info(f"═══ LSTM Retrain Complete — {duration}, val acc {metrics['val_accuracy']:.1%} ═══")

    except Exception as e:
        logger.error(f"LSTM retrain failed with exception: {e}")
        notifier._send(f"⚠️ *LSTM Retrain Error*\n{e}")
    finally:
        _retrain_running = False
        _retrain_lock.release()


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

    config.validate()
    instance_manager.start()
    notifier.startup_message()

    # BackgroundScheduler runs in a daemon thread, freeing the main thread
    # for the Telegram polling loop (which requires the main thread)
    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(
        scan_markets, "interval",
        minutes=config.SCAN_INTERVAL_MINUTES,
        id="market_scan", name="Market Scan"
    )

    scheduler.add_job(
        monitor_positions, "interval",
        minutes=5,
        id="position_monitor", name="Position Monitor"
    )

    eod_eval_h,        eod_eval_m   = config.EOD_EVALUATION_TIME.split(":")
    eod_close_h,       eod_close_m  = config.EOD_CLOSE_TIME.split(":")
    report_h,          report_m     = config.DAILY_REPORT_TIME.split(":")
    weekly_report_h,   weekly_report_m   = config.WEEKLY_REPORT_TIME.split(":")
    weekly_analysis_h, weekly_analysis_m = config.WEEKLY_ANALYSIS_TIME.split(":")

    scheduler.add_job(
        eod_evaluation,
        CronTrigger(hour=int(eod_eval_h), minute=int(eod_eval_m)),
        id="eod_evaluation", name="EOD Evaluation"
    )

    scheduler.add_job(
        force_close_all,
        CronTrigger(hour=int(eod_close_h), minute=int(eod_close_m)),
        id="force_close", name="Force Close All"
    )

    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=int(report_h), minute=int(report_m)),
        id="daily_report", name="Daily Report"
    )

    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=int(weekly_analysis_h), minute=int(weekly_analysis_m)),
        id="weekly_analysis", name="Weekly Analysis"
    )

    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=int(weekly_report_h), minute=int(weekly_report_m)),
        id="weekly_report", name="Weekly Report"
    )

    scheduler.add_job(
        send_daily_plan,
        CronTrigger(hour=0, minute=10),
        id="daily_plan", name="Tomorrow's Trading Plan"
    )

    # LSTM continuous retrain — runs on a rolling interval
    # Starts at 4h (240 min), tighten as we learn training speed on this hardware
    if config.LSTM_ENABLED and config.LSTM_RETRAIN_INTERVAL_MIN > 0:
        scheduler.add_job(
            retrain_lstm,
            "interval",
            minutes=config.LSTM_RETRAIN_INTERVAL_MIN,
            id="lstm_retrain",
            name=f"LSTM Retrain (every {config.LSTM_RETRAIN_INTERVAL_MIN}min)"
        )

    logger.info("📅 Scheduler started with the following jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"   - {job.name}")

    scheduler.start()

    # Run initial market scan in a background thread so it doesn't
    # block the Telegram polling loop from starting
    threading.Thread(target=scan_markets, daemon=True).start()

    # Run Telegram chat handler in the main thread — this is required because
    # set_wakeup_fd (used internally by python-telegram-bot) only works in
    # the main thread of the main interpreter
    if os.getenv("DISABLE_TELEGRAM", "").lower() in ("1", "true", "yes"):
        logger.info("📵 Telegram disabled (DISABLE_TELEGRAM=1) — running scheduler only")
        try:
            import signal
            signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown())
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            scheduler.shutdown()
    else:
        logger.info("🤖 Starting Telegram chat interface in main thread...")
        try:
            chat_app = chat_handler.build_app()
            chat_app.run_polling(drop_pending_updates=True)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            scheduler.shutdown()
            notifier._send("⚠️ *Bot Stopped* — manually stopped by user.")
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            scheduler.shutdown()


if __name__ == "__main__":
    main()