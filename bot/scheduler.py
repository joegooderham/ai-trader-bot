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
from datetime import datetime, timezone, timedelta

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
from bot.engine.lstm.drift import DriftDetector
from bot.analytics.metrics import MetricsEngine
from bot.analytics.integrity_monitor import IntegrityMonitor
from broker.ig_streaming import IGStreamingClient
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

# Real-time position streaming via IG Lightstreamer (BACKLOG-004 / GH#6)
streaming_client = IGStreamingClient(broker)

# Analytics — drift detection and metrics computation (Phase 2)
drift_detector = DriftDetector()
metrics_engine = MetricsEngine()

# Profit integrity monitor — proactive detection of trading anomalies
# Runs at three frequencies: quick (per-scan), hourly, and deep (4-hourly)
integrity_monitor = IntegrityMonitor(notifier=notifier)

# Track last reported P&L per position to only alert on significant changes
_last_reported_pl: dict = {}

MCP_SERVER_URL = "http://mcp-server:8090"

# ── Static Correlation Matrix (BACKLOG-005) ──────────────────────────────────
# Approximate pairwise correlations between major forex pairs.
# Positive = move together, Negative = move opposite.
# Used to block opening a new position when we already hold a highly correlated pair.
# Values sourced from long-run 5Y daily correlation data (updated periodically).
CORRELATION_MATRIX = {
    ("EUR_USD", "GBP_USD"):  0.85,   # Both are USD-counter pairs, move similarly
    ("EUR_USD", "AUD_USD"):  0.70,   # Both anti-USD, moderate correlation
    ("EUR_USD", "NZD_USD"):  0.65,
    ("EUR_USD", "USD_CAD"): -0.80,   # Inversely correlated (USD base vs counter)
    ("EUR_USD", "USD_CHF"): -0.90,   # Strong inverse — classic hedge pair
    ("EUR_USD", "USD_JPY"): -0.55,
    ("GBP_USD", "AUD_USD"):  0.60,
    ("GBP_USD", "USD_CAD"): -0.70,
    ("GBP_USD", "USD_CHF"): -0.75,
    ("GBP_USD", "USD_JPY"): -0.45,
    ("AUD_USD", "NZD_USD"):  0.90,   # Commodity bloc pairs, very high correlation
    ("AUD_USD", "USD_CAD"): -0.65,
    ("USD_JPY", "EUR_JPY"):  0.80,   # Both JPY-cross pairs
    ("USD_JPY", "GBP_JPY"):  0.75,
    ("EUR_JPY", "GBP_JPY"):  0.90,
}


def _get_current_session() -> str:
    """Determine which trading session is currently active (UTC)."""
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour < 17:
        return "overlap"
    elif 8 <= hour < 17:
        return "london"
    elif 13 <= hour < 22:
        return "new_york"
    elif 0 <= hour < 9:
        return "tokyo"
    else:
        return "sydney"


def _get_session_min_confidence(pair: str) -> float:
    """
    Get the effective minimum confidence for the current session (BACKLOG-006).

    During quiet sessions (Sydney, Tokyo), the threshold is raised to filter out
    weak signals that are unreliable in low-volume conditions.
    JPY pairs are exempt from the Tokyo penalty since they're most active then.
    """
    session = _get_current_session()
    boost = config.SESSION_CONFIDENCE_BOOST.get(session, 0)

    # JPY pairs are exempt from the Tokyo session penalty
    if session == "tokyo" and config.SESSION_JPY_EXEMPT and "JPY" in pair:
        boost = 0

    effective_min = config.MIN_CONFIDENCE_SCORE + boost
    if boost != 0:
        logger.debug(
            f"{pair} session adjustment: {session} session, "
            f"min confidence {config.MIN_CONFIDENCE_SCORE}% + {boost:+d} = {effective_min}%"
        )
    return effective_min


def _get_correlation(pair_a: str, pair_b: str) -> float:
    """Look up correlation between two pairs. Returns 0 if unknown."""
    return (
        CORRELATION_MATRIX.get((pair_a, pair_b))
        or CORRELATION_MATRIX.get((pair_b, pair_a))
        or 0.0
    )

# ── Circuit Breaker State ──────────────────────────────────────────────────────
# Tracks daily drawdown to pause trading if losses exceed the configured threshold.
# Resets at EOD close (23:59 UTC) when force_close_all() runs.
_circuit_breaker_until: datetime = None
_day_start_balance: float = None
_day_start_date: str = None

# Manual pause flag — set via /pause Telegram command, cleared via /resume
_trading_paused: bool = False


def _check_circuit_breaker() -> bool:
    """
    Check if the daily loss circuit breaker should activate.
    Returns True if trading should be paused.

    How it works:
    - Records the account balance at the start of each trading day
    - If balance drops by more than DAILY_LOSS_CIRCUIT_BREAKER_PCT, pauses for 24h
    - Resets at EOD close so the next day starts fresh
    """
    global _circuit_breaker_until, _day_start_balance, _day_start_date

    # If circuit breaker is already active, check if it's expired
    if _circuit_breaker_until:
        if datetime.now(timezone.utc) < _circuit_breaker_until:
            return True
        else:
            logger.info("Circuit breaker expired — resuming trading")
            _circuit_breaker_until = None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_balance = broker.get_account_balance()

    # Record starting balance once per day (first scan of the day)
    if _day_start_date != today:
        _day_start_balance = current_balance
        _day_start_date = today
        logger.info(f"Day start balance recorded: £{_day_start_balance:.2f}")
        return False

    if _day_start_balance and _day_start_balance > 0:
        drawdown_pct = ((_day_start_balance - current_balance) / _day_start_balance) * 100

        if drawdown_pct >= config.DAILY_LOSS_CIRCUIT_BREAKER_PCT:
            _circuit_breaker_until = datetime.now(timezone.utc) + timedelta(hours=24)
            logger.warning(
                f"CIRCUIT BREAKER ACTIVATED — account down {drawdown_pct:.1f}% today "
                f"(£{_day_start_balance:.2f} → £{current_balance:.2f}). "
                f"Trading paused until {_circuit_breaker_until.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            notifier._send_system(
                f"🚨 *CIRCUIT BREAKER ACTIVATED*\n"
                f"─────────────────────────────\n"
                f"Account down *{drawdown_pct:.1f}%* today\n"
                f"Start: £{_day_start_balance:.2f} → Now: £{current_balance:.2f}\n"
                f"Trading paused until {_circuit_breaker_until.strftime('%H:%M UTC tomorrow')}\n\n"
                f"_Threshold: {config.DAILY_LOSS_CIRCUIT_BREAKER_PCT}%_"
            )
            return True

    return False


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

    # Auto-resume: if trading was paused by daily profit target, resume
    # at the start of a new trading day (after EOD close at 23:59)
    global _trading_paused
    if _trading_paused and getattr(scan_markets, '_profit_target_date', None):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != scan_markets._profit_target_date:
            _trading_paused = False
            scan_markets._profit_target_date = None
            logger.info("Trading auto-resumed — new trading day")
            notifier._send_system("*▶️ Trading auto-resumed* — new trading day started.")

    # Manual pause: user can pause trading via /pause Telegram command
    if _trading_paused:
        logger.info("Trading paused — skipping market scan")
        return

    # Circuit breaker: pause all trading if daily drawdown exceeds threshold
    if _check_circuit_breaker():
        logger.warning("Circuit breaker active — skipping market scan")
        return

    logger.info(f"─── Market Scan Started ({_get_current_session().upper()} session) ───")

    balance = broker.get_account_balance()
    deployed_capital = broker.get_open_positions_value()
    available_capital = min(config.MAX_CAPITAL - deployed_capital, balance)

    if available_capital <= 0:
        logger.info(f"Capital limit reached (£{config.MAX_CAPITAL} deployed). Skipping scan.")
        return

    open_trades = broker.get_open_trades()
    open_pairs = {t.get("instrument") for t in open_trades}

    # 0 = unlimited positions (learning/demo mode)
    if config.MAX_OPEN_POSITIONS > 0 and len(open_pairs) >= config.MAX_OPEN_POSITIONS:
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
    # Remediation guard: skip pairs disabled at runtime by the integrity monitor
    if pair in config.DISABLED_PAIRS:
        logger.info(f"Skipping {pair} — disabled by remediation system")
        return

    # BACKLOG-005: Correlation hard block — don't open a position if we already
    # hold a highly correlated pair (avoids doubling the same directional bet)
    open_trades = broker.get_open_trades()
    for held in open_trades:
        held_pair = held.get("pair") or held.get("instrument")
        if held_pair and held_pair != pair:
            corr = abs(_get_correlation(pair, held_pair))
            if corr >= config.CORRELATION_BLOCK_THRESHOLD:
                logger.info(
                    f"Skipping {pair} — correlation {corr:.2f} with open position "
                    f"{held_pair} exceeds threshold {config.CORRELATION_BLOCK_THRESHOLD}"
                )
                return

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

    # BACKLOG-004: Fetch higher-timeframe candles for trend confirmation
    mtf_context = None
    htf_candles = None
    if config.HTF_TIMEFRAME and config.HTF_TIMEFRAME != "none":
        try:
            htf_candles = broker.get_candles(
                pair, count=config.HTF_LOOKBACK_CANDLES, granularity=config.HTF_TIMEFRAME
            )
            if htf_candles is not None and len(htf_candles) >= 50:
                mtf_context = indicators.calculate_trend_summary(htf_candles)
                logger.debug(f"{pair} HTF({config.HTF_TIMEFRAME}): {mtf_context}")
        except Exception as e:
            logger.debug(f"HTF fetch failed for {pair}: {e}")

    # Get LSTM prediction if model is loaded and enabled
    # Pass MCP context and HTF candles so the LSTM can use enhanced features
    # (sentiment, COT, FRED, volatility, HTF trend) alongside technicals
    ml_prediction = None
    if lstm_predictor:
        ml_prediction = lstm_predictor.predict(
            pair, candles,
            mcp_context=mcp_context,
            htf_df=htf_candles
        )

    if config.LSTM_SHADOW_MODE and ml_prediction:
        # Shadow mode: score WITH and WITHOUT LSTM, log both, but only act on indicator-only
        lstm_result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context,
            ml_prediction=ml_prediction, mtf_context=mtf_context
        )
        indicator_result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context,
            ml_prediction=None, mtf_context=mtf_context
        )

        # Log the comparison so we can see if LSTM is adding value
        diff = lstm_result.score - indicator_result.score
        diff_str = f"+{diff:.1f}" if diff >= 0 else f"{diff:.1f}"
        logger.info(
            f"{pair} SHADOW | LSTM: {lstm_result.score:.1f}% {lstm_result.direction} | "
            f"Indicators: {indicator_result.score:.1f}% {indicator_result.direction} | "
            f"Delta: {diff_str}pp | LSTM pred: {ml_prediction}"
        )

        # Log prediction to SQLite for accuracy tracking
        try:
            storage.save_prediction({
                "pair": pair,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "predicted_direction": ml_prediction["direction"],
                "predicted_probability": ml_prediction["probability"],
                "confidence_score": lstm_result.score,
                "indicator_only_score": indicator_result.score,
                "model_version": getattr(lstm_predictor, 'model_version', None),
                "confidence_breakdown": lstm_result.breakdown,
            })
        except Exception as e:
            logger.debug(f"Failed to save prediction: {e}")

        # Use indicator-only result for actual trade decisions until shadow mode is off
        result = indicator_result
    else:
        # Live mode: LSTM score drives real trade decisions (50% weight)
        result = confidence.calculate_confidence(
            pair=pair, indicators=ind, mcp_context=mcp_context,
            ml_prediction=ml_prediction, mtf_context=mtf_context
        )

        # Log LSTM prediction when not in shadow mode
        if ml_prediction:
            try:
                storage.save_prediction({
                    "pair": pair,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "predicted_direction": ml_prediction["direction"],
                    "predicted_probability": ml_prediction["probability"],
                    "confidence_score": result.score,
                    "indicator_only_score": None,
                    "model_version": getattr(lstm_predictor, 'model_version', None),
                    "confidence_breakdown": result.breakdown,
                })
            except Exception as e:
                logger.debug(f"Failed to save prediction: {e}")

    # BACKLOG-006: Apply session-aware minimum confidence
    # During quiet sessions the bar is raised, during peak sessions it can be lowered
    session_min = _get_session_min_confidence(pair)
    session_trade = result.score >= session_min
    session = _get_current_session()

    logger.info(
        f"{pair}: {result.direction} | Confidence: {result.score:.1f}% | "
        f"Session: {session} (min: {session_min:.0f}%) | Trade: {session_trade}"
    )

    # ── Scan Audit Log — record EVERY evaluation for review ─────────────────
    # Saves the full decision context whether we trade or not, so you can
    # review "why didn't the bot trade here?" via the dashboard scan log.
    skip_reason = None
    if not session_trade:
        skip_reason = f"confidence {result.score:.0f}% < session min {session_min:.0f}%"
    elif result.direction in config.DISABLED_DIRECTIONS:
        skip_reason = f"{result.direction} direction disabled"

    try:
        storage.save_scan_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "direction": result.direction,
            "confidence_score": result.score,
            "traded": session_trade and skip_reason is None,
            "skip_reason": skip_reason,
            "indicators": {
                "rsi": ind.rsi, "macd_signal": ind.macd_signal,
                "ema_trend": ind.ema_trend, "bb_position": ind.bb_position,
                "atr": ind.atr, "current_price": ind.current_price,
            },
            "mcp_context": {k: v for k, v in mcp_context.items() if k != "timestamp"} if mcp_context else None,
            "lstm_prediction": ml_prediction,
            "breakdown": result.breakdown,
            "reasoning": result.reasoning[:500] if result.reasoning else None,
        })
    except Exception as e:
        logger.debug(f"Scan log save failed: {e}")

    if not session_trade:
        return

    # Remediation guard: skip disabled directions (set by integrity monitor)
    if result.direction in config.DISABLED_DIRECTIONS:
        logger.info(
            f"Skipping {pair} {result.direction} — direction disabled by remediation system"
        )
        return

    size, stop_loss_price, take_profit_price = calculate_position_size(
        pair=pair,
        direction=result.direction,
        entry_price=ind.current_price,
        atr=ind.atr,
        available_capital=available_capital,
        confidence_score=result.score
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
        # Enrich trade_result with confidence data before persisting
        # These fields aren't available inside ig_client, so we add them here
        trade_result["confidence_score"] = result.score
        trade_result["reasoning"] = result.reasoning

        trade_number = storage.save_trade(trade_result)

        notifier.trade_opened(
            pair=pair,
            direction=result.direction,
            fill_price=trade_result["fill_price"],
            units=size,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            confidence_score=result.score,
            breakdown=result.breakdown,
            reasoning=result.reasoning,
            trade_number=trade_number
        )

        # Quick integrity check — validate the trade's risk parameters immediately
        # Catches SL/TP bugs before they compound (e.g. the breakeven bug)
        integrity_monitor.quick_check(trade_result)


def _on_streaming_position_update(position: dict):
    """
    Callback fired by Lightstreamer when an open position changes in real-time.

    This replaces the 5-minute polling for position P&L monitoring.
    Only sends Telegram alerts when the P&L change exceeds the threshold
    to avoid spamming on every tiny price tick.
    """
    global _last_reported_pl

    deal_id = position.get("dealId")
    pair = position.get("pair", "")
    upl = position.get("unrealizedPL")
    status = position.get("status")

    if not deal_id:
        return

    # If the position was closed (status = DELETED), persist to DB and clean up.
    # This catches stop-loss and take-profit hits that happen on IG's servers —
    # without this, the trade stays "open" in SQLite and the dashboard is wrong.
    if status == "DELETED":
        logger.info(f"Position closed via streaming: {pair} (deal {deal_id})")
        # Persist the final P&L and close timestamp to the database
        if deal_id:
            try:
                final_pl = upl or _last_reported_pl.get(deal_id, 0)
                storage.update_trade_field(deal_id, "pl", final_pl)
                storage.update_trade_field(deal_id, "closed_at", datetime.now(timezone.utc).isoformat())
                storage.update_trade_field(deal_id, "status", "CLOSED")
                storage.update_trade_field(deal_id, "close_reason", "stop_or_tp")
                logger.info(f"Persisted close for {pair}: P&L £{final_pl:.2f}")
            except Exception as e:
                logger.warning(f"Failed to persist streaming close for {deal_id}: {e}")
        _last_reported_pl.pop(deal_id, None)

        close_price = position.get("closePrice") or position.get("level")
        pl = position.get("pl") or position.get("profit", 0)
        storage.update_trade(deal_id, {
            "close_price": close_price,
            "pl": pl,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "close_reason": "Broker closed (stop/TP hit)",
            "status": "CLOSED",
        })
        logger.info(f"DB updated for streamed close: {pair} deal={deal_id} pl={pl}")

        # Send Telegram notification so the user knows about stop/TP hits
        trade_num = storage.get_trade_number(deal_id)
        try:
            balance = broker.get_account_balance()
        except Exception:
            balance = None
        notifier.trade_closed(
            pair=pair,
            direction=position.get("direction", "N/A"),
            close_price=close_price or 0,
            pl=pl,
            reason="Stop/TP hit (streaming)",
            account_balance=balance,
            trade_number=trade_num,
        )
        return

    # Check if P&L has changed significantly since last alert
    if upl is not None:
        last_pl = _last_reported_pl.get(deal_id, 0)
        pl_change = abs(upl - last_pl)

        if pl_change >= config.STREAMING_PL_ALERT_THRESHOLD:
            direction = position.get("direction", "")
            emoji = "📈" if upl > last_pl else "📉"
            logger.info(
                f"Streaming: {pair} {direction} | P&L: £{upl:.2f} "
                f"(change: £{upl - last_pl:+.2f})"
            )

            # Send Telegram alert for significant P&L changes so user sees all activity
            pair_display = pair.replace("_", "/")
            pl_sign = "+" if upl >= 0 else ""
            change_sign = "+" if upl > last_pl else ""
            notifier._send(
                f"{emoji} *P&L Update — {pair_display}*\n"
                f"Direction: {direction}\n"
                f"P&L: *{pl_sign}£{upl:.2f}* ({change_sign}£{upl - last_pl:.2f})"
            )

            _last_reported_pl[deal_id] = upl

    # Also apply trailing stop logic on every streaming update
    # This makes trailing stops react in real-time instead of every 5 min
    _update_trailing_stop(position)


def _on_streaming_confirmation(confirmation: dict):
    """Callback fired by Lightstreamer when a trade is confirmed."""
    status = confirmation.get("dealStatus")
    epic = confirmation.get("epic", "")
    direction = confirmation.get("direction", "")
    level = confirmation.get("level")

    if status == "ACCEPTED":
        logger.info(f"Streaming confirmation: {epic} {direction} @ {level}")
    elif status == "REJECTED":
        reason = confirmation.get("reason", "Unknown")
        logger.warning(f"Streaming rejection: {epic} {direction} — {reason}")


def monitor_positions():
    """
    Monitor all open positions every 5 minutes via REST polling.

    This is the fallback for when Lightstreamer streaming is unavailable.
    When streaming IS active, this still runs as a safety net to catch
    any updates that might have been missed and to apply trailing stops.

    Also checks daily profit target — if total P&L for the day hits the
    target, closes everything and pauses trading for the rest of the day.
    """
    global _trading_paused

    open_trades = broker.get_open_trades()

    # ── Daily Profit Target ──────────────────────────────────────────────
    # If today's realised + unrealised P&L hits the target, bank it all.
    # Close everything and pause until tomorrow's first scan.
    daily_target = config._cfg.get("risk", {}).get("daily_profit_target", 0)
    if daily_target > 0 and not _trading_paused:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_trades = storage.get_trades_for_date(today)
            realised_pl = sum(t.get("pl", 0) for t in today_trades if t.get("closed_at"))
            unrealised_pl = sum(float(t.get("unrealizedPL", 0)) for t in (open_trades or []))
            total_pl = realised_pl + unrealised_pl

            if total_pl >= daily_target:
                logger.info(f"DAILY PROFIT TARGET HIT: £{total_pl:.2f} >= £{daily_target} — banking profits")

                # Close all open positions
                results = broker.close_all_positions()
                balance = 0
                try:
                    balance = broker.get_account_balance()
                except Exception:
                    pass

                banked_pl = 0
                for r in results:
                    deal_id = r.get("deal_id")
                    pl = r.get("pl", 0)
                    banked_pl += pl
                    if deal_id:
                        storage.update_trade(deal_id, {
                            "close_price": r.get("close_price"),
                            "pl": pl,
                            "closed_at": r.get("closed_at", datetime.now(timezone.utc).isoformat()),
                            "close_reason": f"Daily profit target (£{daily_target})",
                            "status": "CLOSED",
                        })
                        trade_num = storage.get_trade_number(deal_id)
                        notifier.trade_closed(
                            pair=r.get("pair", "Unknown"),
                            direction="N/A",
                            close_price=r.get("close_price", 0),
                            pl=pl,
                            reason=f"Daily profit target (£{daily_target})",
                            account_balance=balance,
                            trade_number=trade_num,
                        )

                # Pause trading for the rest of the day — auto-resumes tomorrow
                _trading_paused = True
                scan_markets._profit_target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                notifier._send_system(
                    f"*💰 DAILY PROFIT TARGET HIT — BANKED*\n"
                    f"═════════════════════\n"
                    f"*Realised today:* +£{realised_pl:.2f}\n"
                    f"*Just closed:* +£{banked_pl:.2f}\n"
                    f"*Total:* +£{total_pl:.2f}\n"
                    f"*Target:* £{daily_target}\n"
                    f"─────────────────────\n"
                    f"All positions closed. Trading paused until tomorrow.\n"
                    f"_Use /resume to restart early if needed._\n"
                    f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
                )

                return  # Don't continue monitoring — everything is closed
        except Exception as e:
            logger.error(f"Daily profit target check failed: {e}")

    # ── Position Reconciliation ─────────────────────────────────────────────
    # Every 5 minutes, check that DB and IG agree on what's open.
    # If IG closed a position (stop/TP hit) but the DB missed the update,
    # mark it as closed. This is the single source of truth mechanism.
    try:
        ig_deal_ids = {t.get("dealId") for t in (open_trades or [])}
        db_open = storage.get_open_trades_from_db()
        for db_trade in db_open:
            deal_id = db_trade.get("deal_id") or db_trade.get("trade_id")
            if deal_id and deal_id not in ig_deal_ids:
                pair = db_trade.get("pair", "Unknown")
                logger.warning(f"Reconciliation: #{db_trade.get('id')} {pair} ({deal_id}) closed on IG but DB still shows open — fixing")
                storage.update_trade(deal_id, {
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "close_reason": "Broker closed (stop/TP hit)",
                    "status": "CLOSED",
                    "pl": 0,  # Unknown P&L — IG doesn't tell us retrospectively
                })
    except Exception as e:
        logger.debug(f"Position reconciliation failed: {e}")

    if not open_trades:
        return

    for trade in open_trades:
        unrealised_pl = float(trade.get("unrealizedPL", 0))
        pair = trade.get("pair") or trade.get("instrument")
        deal_id = trade.get("dealId")
        logger.debug(f"Position {pair} | Unrealised P&L: £{unrealised_pl:.2f}")

        # Enrich with confidence score from DB for tiered trailing stop params
        if deal_id and not trade.get("confidence_score"):
            try:
                db_trade = storage.get_trade_by_deal_id(deal_id)
                if db_trade and db_trade.get("confidence_score"):
                    trade["confidence_score"] = db_trade["confidence_score"]
            except Exception:
                pass

        # Partial profit-taking: close half the position when 50% of TP is reached
        _check_partial_take_profit(trade)

        # Trailing stop-loss (BACKLOG-007): move stop closer as price moves in our favour
        _update_trailing_stop(trade)


def _check_partial_take_profit(trade: dict):
    """
    Partial profit-taking: when price reaches a percentage of the TP distance,
    close a portion of the position to bank profit. The rest rides with trailing stop.

    This lets us lock in gains while keeping upside exposure. IG supports partial
    closes natively — just close with a smaller size than the full position.

    Uses a SQLite flag (breakdown field) to track whether partial TP has already fired
    for this trade, so we only do it once.
    """
    if not config.PARTIAL_TP_ENABLED:
        return

    pair = trade.get("pair") or trade.get("instrument")
    deal_id = trade.get("dealId")
    direction = trade.get("direction", "BUY")
    entry_price = float(trade.get("level") or trade.get("price", 0))
    current_price = float(trade.get("currentPrice") or entry_price)
    size = float(trade.get("dealSize", 0))
    limit_level = trade.get("limitLevel")  # Take-profit price

    if not entry_price or not current_price or not limit_level or size <= 1:
        return  # Can't partial close 1 contract (IG minimum), need at least 2

    tp_price = float(limit_level)

    # Calculate how far price has moved towards TP
    if direction == "BUY":
        tp_distance = tp_price - entry_price
        price_progress = current_price - entry_price
    else:
        tp_distance = entry_price - tp_price
        price_progress = entry_price - current_price

    if tp_distance <= 0:
        return

    progress_pct = price_progress / tp_distance

    # Check if we've reached the partial TP threshold (e.g. 50% of TP distance)
    if progress_pct < config.PARTIAL_TP_PCT:
        return

    # Check if we already took partial profit on this trade
    # We use the DB to track this — check for a "PARTIAL_TP" flag in the trade record
    try:
        db_trade = storage.get_trade_by_deal_id(deal_id)
        if db_trade and db_trade.get("breakdown") and "PARTIAL_TP" in str(db_trade.get("breakdown", "")):
            return  # Already did partial close
    except Exception:
        pass

    # Calculate partial close size — close e.g. 50% of position
    close_size = round(size * config.PARTIAL_CLOSE_PCT, 1)
    close_size = max(1.0, close_size)  # At least 1 contract
    close_size = min(close_size, size - 1.0)  # Leave at least 1 contract open

    if close_size <= 0:
        return

    logger.info(
        f"Partial TP: {pair} {direction} | Progress: {progress_pct:.0%} of TP | "
        f"Closing {close_size}/{size} contracts to bank profit"
    )

    result = broker.close_trade(deal_id, close_size, direction)
    if result:
        # Mark this trade as having taken partial profit and record the P&L
        try:
            partial_pl = result.get("pl") or result.get("profit_loss", 0)
            existing_breakdown = str(db_trade.get("breakdown", "")) if db_trade else ""
            storage.update_trade_field(
                deal_id, "breakdown",
                f"{existing_breakdown}|PARTIAL_TP:{close_size}@{current_price:.5f}|PL:{partial_pl:.2f}"
            )
        except Exception as e:
            logger.debug(f"Failed to mark partial TP in DB: {e}")

        notifier._send(
            f"💰 *Partial Profit Taken*\n"
            f"Pair: {pair} ({direction})\n"
            f"Closed: {close_size} of {size} contracts\n"
            f"Entry: {entry_price:.5f}\n"
            f"Closed at: {current_price:.5f}\n"
            f"Progress: {progress_pct:.0%} of take-profit\n"
            f"Remaining: {size - close_size} contracts riding with trailing stop"
        )


def _update_trailing_stop(trade: dict):
    """
    Check if a position qualifies for a trailing stop-loss update.

    Activation: price must have moved >= trailing_stop_activation_atr × ATR from entry.
    Trail distance: stop is set at current_price - trail_atr × ATR (for BUY)
                    or current_price + trail_atr × ATR (for SELL).
    Only moves the stop in the profitable direction — never loosens it.
    """
    pair = trade.get("pair") or trade.get("instrument")
    deal_id = trade.get("dealId")
    direction = trade.get("direction", "BUY")
    entry_price = float(trade.get("level") or trade.get("price", 0))
    current_price = float(trade.get("currentPrice") or entry_price)
    current_stop = trade.get("stopLevel")

    if not entry_price or not current_price:
        return

    # Fetch ATR for this pair to calculate dynamic stop distances
    try:
        candles = broker.get_candles(pair, count=config.LOOKBACK_CANDLES, granularity=config.TIMEFRAME)
        if candles is None or len(candles) < 14:
            return
        ind = indicators.calculate(candles)
        atr = ind.atr
    except Exception as e:
        logger.debug(f"Could not calculate ATR for trailing stop on {pair}: {e}")
        return

    # Use confidence-tiered trailing stop parameters if the trade has a confidence score
    # This way high-conviction trades activate trailing sooner and trail tighter
    confidence_score = trade.get("confidence_score") or 60.0
    from risk.position_sizer import get_trailing_params
    activation_atr, trail_atr = get_trailing_params(confidence_score)
    activation_distance = activation_atr * atr
    trail_distance = trail_atr * atr

    if direction == "BUY":
        price_move = current_price - entry_price
        if price_move < activation_distance:
            return  # Not enough profit to activate trailing stop

        new_stop = round(current_price - trail_distance, 5)

        # Only move stop UP for a BUY — never loosen
        if current_stop and new_stop <= float(current_stop):
            return
    else:
        price_move = entry_price - current_price
        if price_move < activation_distance:
            return

        new_stop = round(current_price + trail_distance, 5)

        # Only move stop DOWN for a SELL — never loosen
        if current_stop and new_stop >= float(current_stop):
            return

    # Update the stop-loss on IG
    logger.info(
        f"Trailing stop: {pair} {direction} | Entry: {entry_price:.5f} | "
        f"Current: {current_price:.5f} | Move: {price_move:.5f} | "
        f"Old stop: {current_stop} → New stop: {new_stop:.5f}"
    )
    success = broker.update_stop_loss(deal_id, new_stop)
    if success:
        # Only send Telegram for significant stop moves (>= 10 pips) to avoid spam.
        # Small trailing adjustments happen every 5 minutes and would flood notifications.
        from risk.position_sizer import PIP_SIZE, DEFAULT_PIP_SIZE
        pip_size = PIP_SIZE.get(pair, DEFAULT_PIP_SIZE)
        old_stop_f = float(current_stop) if current_stop else 0
        stop_move_pips = abs(new_stop - old_stop_f) / pip_size if pip_size > 0 else 0

        if stop_move_pips >= 10:
            notifier._send(
                f"📈 *Trailing Stop Updated*\n"
                f"Pair: {pair} ({direction})\n"
                f"Entry: {entry_price:.5f}\n"
                f"Price: {current_price:.5f} (+{price_move:.5f})\n"
                f"New stop: {new_stop:.5f}"
            )


def eod_evaluation():
    """Runs at 23:45 UTC — evaluates all open positions for the 98% overnight rule."""
    logger.info("Running end-of-day position evaluation (98% rule check)")
    eod_manager.evaluate_overnight_holds()


def force_close_all():
    """Runs at 23:59 UTC — closes every remaining open position."""
    global _day_start_balance, _day_start_date, _circuit_breaker_until

    global _trading_paused

    logger.info("Running end-of-day force close")
    # Clear candle cache at day rollover so fresh data loads tomorrow
    broker.clear_candle_cache()
    # Reset circuit breaker state so the next trading day starts fresh
    _day_start_balance = None
    _day_start_date = None
    _circuit_breaker_until = None

    # Reset daily profit target pause — new trading day starts clean
    if _trading_paused and getattr(scan_markets, '_profit_target_date', None):
        _trading_paused = False
        scan_markets._profit_target_date = None
        logger.info("Daily profit target pause reset — new trading day")
    close_results = eod_manager.force_close_non_held_positions()

    if close_results:
        for result in close_results:
            deal_id = result.get("deal_id")
            pl = result.get("pl") or result.get("profit_loss", 0)

            # Persist close data to DB so dashboard P&L is accurate
            if deal_id:
                try:
                    storage.update_trade_field(deal_id, "pl", pl)
                    storage.update_trade_field(deal_id, "closed_at", result.get("closed_at"))
                    storage.update_trade_field(deal_id, "close_price", result.get("close_price", 0))
                    storage.update_trade_field(deal_id, "close_reason", "eod_close")
                    storage.update_trade_field(deal_id, "status", "CLOSED")
                except Exception as e:
                    logger.warning(f"Failed to persist close data for {deal_id}: {e}")

            balance = broker.get_account_balance()
            deal_id = result.get("deal_id")
            # Look up the trade number from when this position was opened
            trade_num = storage.get_trade_number(deal_id)

            # Persist the close data to SQLite so dashboard and reports stay in sync
            if deal_id:
                storage.update_trade(deal_id, {
                    "close_price": result.get("close_price"),
                    "pl": result.get("pl") or result.get("profit_loss", 0),
                    "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                    "close_reason": "End of day close",
                    "status": "CLOSED",
                })

            notifier.trade_closed(
                pair=result.get("pair", "Unknown"),
                direction="N/A",
                close_price=result.get("close_price", 0),
                pl=pl,
                reason="End of day close",
                account_balance=balance,
                trade_number=trade_num
            )


def send_daily_plan():
    """Sends tomorrow's trading plan via Telegram."""
    logger.info("Generating tomorrow's trading plan")
    plan = plan_generator.generate()
    notifier._send(plan)


def send_daily_report():
    """Runs at 00:05 UTC — compiles and sends the daily Telegram report."""
    logger.info("Generating daily report")

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
            notifier._send_system(f"⚠️ *LSTM Retrain Failed*\n{metrics['error']}")
            return

        # Reload the predictor with the freshly trained model
        if lstm_predictor:
            lstm_predictor.reload()

        # Persist training metrics to SQLite for drift detection and dashboards (Phase 2)
        try:
            storage.save_model_metrics(metrics)
        except Exception as e:
            logger.debug(f"Failed to save model metrics: {e}")

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
        notifier._send_system(f"⚠️ *LSTM Retrain Error*\n{e}")
    finally:
        _retrain_running = False
        _retrain_lock.release()


def resolve_prediction_outcomes():
    """
    Check unresolved predictions against subsequent candle data to determine
    if the LSTM prediction was correct. Runs hourly.

    Uses the same logic as the labelling function: looks ahead 3 candles and
    checks if price moved >= 1 ATR in the predicted direction.
    """
    unresolved = storage.get_unresolved_predictions(max_age_hours=24)
    if not unresolved:
        return

    resolved_count = 0
    for pred in unresolved:
        try:
            pair = pred["pair"]
            pred_time = pred["timestamp"]
            pred_direction = pred["predicted_direction"]

            # Get candles since the prediction was made
            candles = storage.get_candles(pair, config.TIMEFRAME, count=100)
            if candles is None or len(candles) < 5:
                continue

            # Find the candle at or after the prediction time
            import pandas as pd
            pred_dt = pd.to_datetime(pred_time, utc=True)
            future = candles[candles.index > pred_dt]

            # Need at least 3 candles after the prediction to evaluate
            if len(future) < 3:
                continue

            close_at_pred = candles.loc[candles.index <= pred_dt].iloc[-1]["close"] if len(candles[candles.index <= pred_dt]) > 0 else None
            if close_at_pred is None:
                continue

            # Check the 3 candles after prediction
            look = future.iloc[:3]
            max_high = look["high"].max()
            min_low = look["low"].min()

            upside_pips = max_high - close_at_pred
            downside_pips = close_at_pred - min_low

            # Determine actual direction based on which move was larger
            if upside_pips > downside_pips:
                actual_direction = "BUY"
                actual_pips = upside_pips
            elif downside_pips > upside_pips:
                actual_direction = "SELL"
                actual_pips = downside_pips
            else:
                actual_direction = "HOLD"
                actual_pips = 0

            was_correct = (pred_direction == actual_direction)
            storage.update_prediction_outcome(
                pred["id"], actual_direction, round(actual_pips, 5), was_correct
            )
            resolved_count += 1

        except Exception as e:
            logger.debug(f"Failed to resolve prediction {pred.get('id')}: {e}")

    if resolved_count > 0:
        logger.info(f"Resolved {resolved_count}/{len(unresolved)} prediction outcomes")


def check_drift():
    """
    Run drift detection — checks if LSTM accuracy has degraded.
    If drift is detected, triggers an early retrain and sends Telegram alert.
    Runs every 30 minutes.
    """
    result = drift_detector.check()

    if result["status"] == "drift":
        notifier._send_system(
            f"⚠️ *Model Drift Detected*\n"
            f"─────────────────────────────\n"
            f"Live accuracy: {result['rolling_accuracy_24h']:.1f}%\n"
            f"Training accuracy: {result['training_accuracy']:.1f}%\n"
            f"Delta: {result['drift_delta']:.1f}%\n\n"
            f"Triggering early retrain..."
        )
        # Trigger early retrain in a background thread
        threading.Thread(target=retrain_lstm, daemon=True).start()
    elif result["status"] == "ok":
        logger.debug(f"Drift check OK: {result['message']}")


def compute_analytics():
    """Compute and store rolling analytics metrics. Runs hourly."""
    try:
        metrics_engine.compute_all()
    except Exception as e:
        logger.error(f"Analytics computation failed: {e}")


def integrity_hourly_review():
    """
    Hourly profit integrity check — detects patterns like breakeven streaks,
    win rate collapse, P&L drift, and short trade durations before they compound.
    """
    try:
        result = integrity_monitor.hourly_review()
        status = result.get("status", "UNKNOWN")
        if status == "WARNING":
            logger.warning(f"Integrity hourly review: {len(result.get('issues', []))} issue(s) detected")
        else:
            logger.debug(f"Integrity hourly review: {status}")
    except Exception as e:
        logger.error(f"Integrity hourly review failed: {e}")


def integrity_deep_review():
    """
    Deep integrity analysis every 4 hours — per-pair profitability,
    config effectiveness scoring, and actionable recommendations.
    """
    try:
        result = integrity_monitor.deep_review()
        status = result.get("status", "UNKNOWN")
        if status == "WARNING":
            logger.warning(f"Integrity deep review: {len(result.get('issues', []))} issue(s) detected")
        else:
            logger.debug(f"Integrity deep review: {status}")
    except Exception as e:
        logger.error(f"Integrity deep review failed: {e}")


def weekly_strategy_review():
    """
    Monday 00:15 UTC — compare this week vs last week performance.
    Flags pairs that flipped from profitable to unprofitable and
    recommends defensive adjustments if P&L declined significantly.
    """
    try:
        integrity_monitor.weekly_strategy_review()
        logger.info("Weekly strategy review completed")
    except Exception as e:
        logger.error(f"Weekly strategy review failed: {e}")


def daily_lstm_health():
    """
    Daily 08:00 UTC — LSTM model health check.
    Reports training age, prediction accuracy, edge value, and
    recommends shadow mode toggle based on performance.
    """
    try:
        integrity_monitor.daily_lstm_health()
        logger.info("Daily LSTM health check completed")
    except Exception as e:
        logger.error(f"Daily LSTM health check failed: {e}")


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

    # Start the dashboard command API in a daemon thread — gives the dashboard
    # HTTP access to bot internals (pause/resume, close trades, config changes)
    from bot.command_api import start_command_api
    threading.Thread(
        target=start_command_api,
        args=(broker, notifier, storage, integrity_monitor),
        daemon=True
    ).start()

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

    # ── Analytics Jobs (Phase 2) ─────────────────────────────────────────────
    # Resolve prediction outcomes by checking subsequent candle data
    scheduler.add_job(
        resolve_prediction_outcomes,
        "interval", minutes=60,
        id="resolve_predictions", name="Resolve Prediction Outcomes"
    )

    # Drift detection — checks if model accuracy has degraded
    scheduler.add_job(
        check_drift,
        "interval", minutes=30,
        id="drift_check", name="Model Drift Check"
    )

    # Compute rolling analytics metrics for dashboards and Telegram
    scheduler.add_job(
        compute_analytics,
        "interval", minutes=60,
        id="analytics", name="Analytics Metrics"
    )

    # ── Profit Integrity Monitor (proactive anomaly detection) ────────────
    # Integrity review: aligned with scan interval so it only reviews after new data
    scheduler.add_job(
        integrity_hourly_review,
        "interval", minutes=config.SCAN_INTERVAL_MINUTES,
        id="integrity_hourly", name="Integrity Review"
    )

    # Deep review every 6 hours: per-pair profitability, config effectiveness
    scheduler.add_job(
        integrity_deep_review,
        "interval", minutes=360,
        id="integrity_deep", name="Integrity Deep Review"
    )

    # ── Remediation System Jobs ─────────────────────────────────────────────
    # Weekly strategy review — Monday 00:15 UTC
    # Compares this week vs last: trade count, win rate, P&L per pair
    scheduler.add_job(
        weekly_strategy_review,
        CronTrigger(day_of_week="mon", hour=0, minute=15),
        id="weekly_strategy_review", name="Weekly Strategy Review"
    )

    # Daily LSTM health — every day at 08:00 UTC
    # Checks model age, prediction accuracy, edge, recommends shadow mode toggle
    scheduler.add_job(
        daily_lstm_health,
        CronTrigger(hour=8, minute=0),
        id="daily_lstm_health", name="Daily LSTM Health"
    )

    logger.info("📅 Scheduler started with the following jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"   - {job.name}")

    scheduler.start()

    # Start real-time position streaming if enabled (BACKLOG-004 / GH#6)
    # Falls back to 5-minute REST polling if Lightstreamer fails to connect
    if config.ENABLE_STREAMING and streaming_client.is_available:
        streaming_ok = streaming_client.start(
            on_position_update=_on_streaming_position_update,
            on_confirmation=_on_streaming_confirmation,
        )
        if streaming_ok:
            logger.info("📡 Real-time position streaming active via Lightstreamer")
        else:
            logger.warning("📡 Streaming failed to start — using 5-min REST polling fallback")
    else:
        logger.info("📡 Streaming disabled — using 5-min REST polling for positions")

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
            notifier._send_system("⚠️ *Bot Stopped* — manually stopped by user.")
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            scheduler.shutdown()


if __name__ == "__main__":
    main()