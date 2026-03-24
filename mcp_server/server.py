"""
mcp_server/server.py — MCP Analysis Server
────────────────────────────────────────────
This is the "research desk" that the trading bot consults before making decisions.

It runs as a separate service (its own Docker container) and exposes a simple
HTTP API. The trading bot calls it with questions like:
  "What's the market context for EUR/USD right now?"

The MCP server responds with structured data:
  - Economic calendar events in the next few hours
  - News sentiment for the currency pair
  - Correlation with other pairs the bot might be holding
  - Current volatility regime
  - Historical performance stats for this pair at this time of day

It also uses Claude AI to write a plain-English market summary that
gets included in your daily and weekly Telegram reports.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
from datetime import datetime, timezone
from loguru import logger
import json
from pathlib import Path

from mcp_server import economic_calendar, sentiment, correlations, volatility, session_stats, client_sentiment
from mcp_server import fred_macro, myfxbook_sentiment, cot_positioning
from bot import config

app = FastAPI(title="AI Trader MCP Server", version="1.0.0")

# In-memory cache to avoid re-fetching data on every single request
_cache = {}
CACHE_DURATION_SECONDS = config.MCP_CONFIG.get("cache_duration_minutes", 30) * 60

# Shared IG client — reused across all MCP modules to avoid creating a new
# authenticated session on every request. Previously each module created its
# own IGClient(), burning through IG's auth rate limit (240 auth calls/hour
# on 5-min scans with 10 pairs × 2 modules).
_shared_ig_client = None

def get_shared_ig_client():
    """Get or create the shared IG client for the MCP server."""
    global _shared_ig_client
    if _shared_ig_client is None:
        try:
            from broker.ig_client import IGClient
            _shared_ig_client = IGClient()
            logger.info("MCP shared IG client initialised")
        except Exception as e:
            logger.warning(f"Failed to create shared IG client: {e}")
    return _shared_ig_client


@app.get("/health")
async def health_check():
    """Simple health endpoint — used by Docker to verify the server is running."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/test-fallback")
async def test_yfinance_fallback():
    """
    Test endpoint to verify yfinance fallback is working.

    Fetches 10 candles from yfinance for all configured pairs and returns
    the results. Use this to confirm the fallback data source is healthy
    without needing to wait for an actual IG failure.

    Example: GET /test-fallback
    """
    import yfinance as yf
    from broker.ig_client import YFINANCE_TICKERS, YFINANCE_INTERVALS

    results = {}
    granularity = config.TIMEFRAME if hasattr(config, "TIMEFRAME") else "H1"
    interval = YFINANCE_INTERVALS.get(granularity, "1h")

    for pair in config.PAIRS:
        ticker_symbol = YFINANCE_TICKERS.get(pair)
        if not ticker_symbol:
            results[pair] = {"status": "error", "reason": "no ticker mapping"}
            continue

        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(period="5d", interval=interval)

            if df is None or df.empty:
                results[pair] = {"status": "error", "reason": "no data returned"}
            else:
                # Return the last 3 candles as a sample
                sample = df.tail(3)
                results[pair] = {
                    "status": "ok",
                    "ticker": ticker_symbol,
                    "candles_available": len(df),
                    "latest_close": round(float(sample.iloc[-1]["Close"]), 5),
                    "latest_time": str(sample.index[-1]),
                }
        except Exception as e:
            results[pair] = {"status": "error", "reason": str(e)}

    all_ok = all(r.get("status") == "ok" for r in results.values())

    return JSONResponse(content={
        "test": "yfinance_fallback",
        "overall_status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pairs": results,
    })


@app.get("/context/{pair}")
async def get_market_context(pair: str):
    """
    Get full market context for a currency pair.

    This is called by the trading bot before every trade decision.
    Returns all analysis data in one response.

    Example: GET /context/EUR_USD
    """
    cache_key = f"context_{pair}"

    # Return cached result if it's fresh enough
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_DURATION_SECONDS:
            logger.debug(f"Returning cached context for {pair} (age: {age:.0f}s)")
            return cached_data

    logger.info(f"Fetching fresh market context for {pair}")

    # Use the shared IG client for modules that need broker data —
    # avoids creating a new authenticated session per module per pair
    ig = get_shared_ig_client()

    # Run all analysis modules concurrently for speed
    results = await asyncio.gather(
        economic_calendar.get_upcoming_events(pair),
        sentiment.get_sentiment(pair),
        correlations.get_correlation_warning(pair),
        volatility.get_volatility_regime(pair, ig_client=ig),
        session_stats.get_session_performance(pair),
        client_sentiment.get_client_sentiment(pair, ig_client=ig),
        fred_macro.get_macro_bias(pair),
        myfxbook_sentiment.get_community_sentiment(pair),
        cot_positioning.get_cot_positioning(pair),
        return_exceptions=True  # Don't fail if one module errors
    )

    # Safely unpack results (handle any module errors gracefully)
    def safe_result(r, default):
        return r if not isinstance(r, Exception) else default

    context = {
        "pair": pair,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "upcoming_high_impact_events": safe_result(results[0], []),
        "sentiment": safe_result(results[1], {pair: "neutral"}),
        "correlation_warning": safe_result(results[2], {}),
        "volatility_regime": safe_result(results[3], "normal"),
        "session_performance": safe_result(results[4], {pair: 50}),
        "client_sentiment": safe_result(results[5], {"contrarian_bias": "NEUTRAL", "bias_strength": 50}),
        "fred_macro": safe_result(results[6], {"bias": "NEUTRAL", "bias_strength": 0}),
        "myfxbook_sentiment": safe_result(results[7], {"contrarian_bias": "NEUTRAL", "bias_strength": 50}),
        "cot_positioning": safe_result(results[8], {"bias": "NEUTRAL", "bias_strength": 0}),
    }

    # Cache the result
    _cache[cache_key] = (datetime.now(timezone.utc), context)

    return JSONResponse(content=context)


@app.get("/weekly-outlook")
async def get_weekly_outlook():
    """
    Generate a full weekly market outlook using Claude AI.

    Called every Sunday evening at 19:00 UTC.
    This gives the bot a strategic view of the week ahead.
    The output is also sent to you via Telegram as your weekly brief.
    """
    logger.info("Generating weekly market outlook with Claude AI")

    # Gather all available context for the weekly analysis
    economic_events = await economic_calendar.get_week_events()
    sentiment_data = {}
    for pair in config.PAIRS:
        sentiment_data[pair] = await sentiment.get_sentiment(pair)

    # Ask Claude AI for intelligent analysis
    claude_analysis = await _ask_claude_for_weekly_outlook(economic_events, sentiment_data)

    return JSONResponse(content={
        "week_starting": _next_monday(),
        "economic_events": economic_events,
        "pair_sentiment": sentiment_data,
        "claude_analysis": claude_analysis,
        "generated_at": datetime.now(timezone.utc).isoformat()
    })


@app.get("/daily-learning")
async def get_daily_learning(date: str = None):
    """
    After the last trade closes each day, the bot calls this endpoint.
    It returns a summary of what market conditions were like today,
    which gets included in the nightly Telegram report.

    Over time, this builds a rich history the AI can learn from.
    """
    data_dir = Path(config.DATA_DIR)
    trades_file = data_dir / "trades.json"

    if not trades_file.exists():
        return {"message": "No trade data yet"}

    with open(trades_file) as f:
        all_trades = json.load(f)

    today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [t for t in all_trades if t.get("opened_at", "").startswith(today)]

    if not today_trades:
        return {"date": today, "message": "No trades today"}

    # What worked today? What didn't?
    wins = [t for t in today_trades if t.get("pl", 0) > 0]
    losses = [t for t in today_trades if t.get("pl", 0) < 0]

    learning = {
        "date": today,
        "total_trades": len(today_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(today_trades) * 100, 1) if today_trades else 0,
        "total_pl": round(sum(t.get("pl", 0) for t in today_trades), 2),
        "best_trade": max(today_trades, key=lambda t: t.get("pl", 0), default={}),
        "worst_trade": min(today_trades, key=lambda t: t.get("pl", 0), default={}),
        "pairs_traded": list(set(t.get("pair") for t in today_trades)),
    }

    return JSONResponse(content=learning)


# ── Analytics API Endpoints (Phase 3) ─────────────────────────────────────────
# These serve both Telegram commands now and the future React dashboard (GH#8).

@app.get("/analytics/model")
async def analytics_model():
    """Current model stats: version, last trained, accuracy, architecture."""
    from data.storage import TradeStorage
    storage = TradeStorage()
    metrics = storage.get_latest_model_metrics()
    history = storage.get_model_history(limit=5)

    return JSONResponse(content={
        "current_model": metrics,
        "recent_history": history,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/analytics/predictions")
async def analytics_predictions(pair: str = None, hours: int = 24):
    """Recent prediction log with outcomes."""
    from data.storage import TradeStorage
    storage = TradeStorage()
    predictions = storage.get_recent_predictions(limit=100)

    # Filter by pair if specified
    if pair:
        predictions = [p for p in predictions if p.get("pair") == pair]

    return JSONResponse(content={
        "predictions": predictions,
        "count": len(predictions),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/analytics/accuracy")
async def analytics_accuracy(window: str = "7d", pair: str = None):
    """Rolling prediction accuracy by pair, direction."""
    from data.storage import TradeStorage
    storage = TradeStorage()

    # Map window string to hours
    window_hours = {"24h": 24, "7d": 168, "30d": 720}.get(window, 168)

    overall = storage.get_prediction_accuracy(hours=window_hours, pair=pair)

    # Per-pair breakdown if no specific pair requested
    pair_breakdown = {}
    if not pair:
        for p in config.PAIRS:
            pair_breakdown[p] = storage.get_prediction_accuracy(hours=window_hours, pair=p)

    return JSONResponse(content={
        "window": window,
        "overall": overall,
        "by_pair": pair_breakdown,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/analytics/drift")
async def analytics_drift():
    """Current drift status from the last check."""
    from bot.engine.lstm.drift import DriftDetector
    detector = DriftDetector()
    result = detector.check()

    return JSONResponse(content=result)


@app.get("/analytics/performance")
async def analytics_performance(window: str = "7d"):
    """Key performance metrics: LSTM edge, accuracy trend, calibration."""
    from data.storage import TradeStorage
    storage = TradeStorage()

    window_hours = {"24h": 24, "7d": 168, "30d": 720}.get(window, 168)

    accuracy = storage.get_prediction_accuracy(hours=window_hours)
    lstm_edge = storage.get_analytics("lstm_edge_avg", hours=window_hours)
    trend = storage.get_analytics("accuracy_trend_weekly", hours=window_hours)
    agreement = storage.get_analytics("lstm_indicator_agreement", hours=window_hours)

    return JSONResponse(content={
        "window": window,
        "accuracy": accuracy,
        "lstm_edge": lstm_edge[-1]["metric_value"] if lstm_edge else None,
        "accuracy_trend": trend[-1]["metric_value"] if trend else None,
        "lstm_indicator_agreement": agreement[-1]["metric_value"] if agreement else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/analytics/summary")
async def analytics_summary():
    """Single endpoint aggregating all analytics for dashboard overview."""
    from data.storage import TradeStorage
    from bot.engine.lstm.drift import DriftDetector
    storage = TradeStorage()
    detector = DriftDetector()

    model = storage.get_latest_model_metrics()
    acc_24h = storage.get_prediction_accuracy(hours=24)
    acc_7d = storage.get_prediction_accuracy(hours=168)
    drift = detector.check()

    # Latest LSTM edge
    edge_data = storage.get_analytics("lstm_edge_avg", hours=24)
    lstm_edge = edge_data[-1]["metric_value"] if edge_data else None

    return JSONResponse(content={
        "model": {
            "version": model.get("model_version") if model else None,
            "val_accuracy": model.get("val_accuracy") if model else None,
            "last_trained": model.get("timestamp") if model else None,
            "feature_count": model.get("feature_count") if model else None,
            "hidden_size": model.get("hidden_size") if model else None,
        },
        "accuracy": {
            "24h": acc_24h,
            "7d": acc_7d,
        },
        "drift": {
            "status": drift["status"],
            "delta": drift.get("drift_delta", 0),
        },
        "lstm_edge": lstm_edge,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def _ask_claude_for_weekly_outlook(economic_events: list, sentiment_data: dict) -> str:
    """
    Use Claude AI to write an intelligent weekly market outlook.

    This is where Claude's reasoning ability adds real value — it can
    connect dots between events, sentiment, and price expectations in a way
    that simple rule-based code cannot.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""You are a professional Forex market analyst. 
    
Here is the market data for the coming week:

ECONOMIC EVENTS:
{json.dumps(economic_events, indent=2)}

CURRENT SENTIMENT BY PAIR:
{json.dumps(sentiment_data, indent=2)}

PAIRS BEING TRADED: {', '.join(config.PAIRS)}

Please provide:
1. A brief overview of the key themes for the week (2-3 sentences)
2. For each pair, one sentence on what to watch for
3. Any specific risks or opportunities that stand out
4. An overall market mood summary (cautious / neutral / opportunistic)

Keep it concise and practical. This will be sent as a Telegram message."""

    try:
        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.MCP_CONFIG.get("max_analysis_tokens", 1000),
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

    except Exception as e:
        logger.error(f"Claude API error during weekly outlook: {e}")
        return "Weekly outlook unavailable — Claude API error. Check logs for details."


def _next_monday() -> str:
    """Returns the date of next Monday as a string."""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_until_monday)).isoformat()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
